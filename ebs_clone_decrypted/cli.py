# -*- coding: utf-8 -*-
"""
Amazon AWS Clone an Encrypted EBS to a Decrypted one

Inputs:
- instance-id
- vol-id

1 - Start aux instance      => Lambda
2 - Stop source instance    => OK
3 - Snapshot source EBS     => OK
4 - Mount source EBS        => OK
5 - Create new EBS  => OK
6 - Mount new EBS   => OK
7 - Prepare source EBS to be copied
8 - Copy data
9 - Unmount source EBS
10 - Delete source EBS
11 - Unmount new EBS
12 - Attach new EBS to source instance
13 - Start source instance
14 - Stop aux instance 

"""
import subprocess

import boto3
import click
import requests


class CloneEbsDecrypted:
    def __init__(self, source_instance_id, source_volume_id, new_size):
        # This class is a refactor of the original procedural code
        # TODO: pass _snapshotON, _snapshot and other properties as parameters
        # Main variables
        self.source_instance_id = source_instance_id
        self.source_volume_id = source_volume_id
        self.new_size = new_size
        self.local_instance_id = None

        # Initialize Boto3 AWS
        self.ec2_client = boto3.client('ec2')
        self.ec2_resource = boto3.resource('ec2')

        # Turn on/off backups
        self._snapshotON = False
        # Devices to be attached on this AUX instance
        self.aux_source_device = '/dev/sds'
        self.aux_target_device = '/dev/sdt'
        # Generated snapshot
        self._snapshot = None
        self.source_device = None
        self.new_volume_id = None

    # Stop instance
    def stop_instance(self, instance_id):
        print("Instance %s Stopping..." % instance_id)
        self.ec2_client.stop_instances(InstanceIds=[instance_id])
        stop_waiter = self.ec2_client.get_waiter('instance_stopped')
        stop_waiter.wait(InstanceIds=[instance_id])
        print("Instance %s Stopped." % instance_id)

    # Generate Snapshot for source volume
    def snapshot(self, volume_id):
        source_volume = self.ec2_resource.Volume(volume_id)

        if self._snapshotON:
            print("Snapshot started... %s" % source_volume)
            self._snapshot = source_volume.create_snapshot(
                Description='EBS-CLONE',
                TagSpecifications=[
                    {'ResourceType': 'snapshot', 'Tags': [{'Key': 'EBS-CLONE', 'Value': 'EBS-CLONE-BKP'}]}
                ]
            )

            snapshot_waiter = self.ec2_client.get_waiter('snapshot_completed')
            snapshot_waiter.wait(Filters=[{'Name': 'volume-id', 'Values': [volume_id]}])
            print("Snapshot completed: %s" % self._snapshot.id)

    # Detach source EBS from source Instance
    def detach_volume(self, detach_volume_id, detach_instance_id):
        source_volume = self.ec2_resource.Volume(detach_volume_id)
        attached_volumes = filter(
            lambda attach: attach['State'] == 'attached' and attach['InstanceId'] == detach_instance_id,
            source_volume.attachments)

        if len(attached_volumes) > 0:
            source_device = attached_volumes.pop()['Device']
            print("Volume %s is being detached..." % detach_volume_id)
            source_volume.detach_from_instance(Device=source_device, InstanceId=detach_instance_id)
            waiter_detach = self.ec2_client.get_waiter('volume_available')
            waiter_detach.wait(VolumeIds=[detach_volume_id])
            print("Volume %s detached." % detach_volume_id)
            return source_device

    # Attach source EBS to Instance
    def attach_volume(self, attach_volume_id, attach_instance_id, attach_device):
        source_volume = self.ec2_resource.Volume(attach_volume_id)
        attached_volumes = filter(lambda attach: attach['State'] == 'attached', source_volume.attachments)

        if len(attached_volumes) == 0:
            print("Volume %s is being attached to instance %s at device %s..." % (
                attach_volume_id, attach_instance_id, attach_device))
            response_attach = source_volume.attach_to_instance(Device=attach_device, InstanceId=attach_instance_id)
            waiter_attach = self.ec2_client.get_waiter('volume_in_use')
            waiter_attach.wait(VolumeIds=[attach_volume_id],
                               Filters=[{'Name': 'attachment.status', 'Values': ['attached']}])
            print("Volume %s attached to instance %s at device %s." % (
                attach_volume_id, attach_instance_id, attach_device))

    # Create EBS From Volume or Snapshot
    def create_volume_from_existing_volume(self, volume_id, new_size=None, snapshot_id=None):
        source_volume = self.ec2_resource.Volume(volume_id)

        if new_size is None:
            new_size = source_volume.size

            # ST1 and SC1 min size is 500
        if source_volume.volume_type in ('sc1', 'st1') and new_size < 500:
            new_size = 500

        new_volume_dict = {
            'AvailabilityZone': source_volume.availability_zone,
            'Encrypted': None,
            'Iops': source_volume.iops,
            'KmsKeyId': None,
            'Size': new_size,
            'VolumeType': source_volume.volume_type,
            'TagSpecifications': [{'ResourceType': 'volume', 'Tags': self.create_tag_specifications(volume_id)}]
        }

        # Remove None attributes
        new_volume_dict = dict(filter(lambda item: item[1] is not None, new_volume_dict.items()))

        # Remove iops attribute if creating GP2
        is_gp2 = False
        if source_volume.volume_type == 'gp2':
            is_gp2 = True
        new_volume_dict = dict((k, v) for k, v in new_volume_dict.iteritems() if k != 'Iops' or not is_gp2)

        # Remove Size and add Snapshot if from snapshot
        if snapshot_id is not None:
            del new_volume_dict['Size']
            new_volume_dict['SnapshotId'] = snapshot_id

        print("Creating new EBS volume... %s" % new_volume_dict)
        response_create_volume = self.ec2_client.create_volume(**new_volume_dict)

        new_volume_id = response_create_volume['VolumeId']
        waiter_create_volume = self.ec2_client.get_waiter('volume_available')
        waiter_create_volume.wait(VolumeIds=[new_volume_id])
        print("New EBS created: \n%s" % response_create_volume)

        return new_volume_id

    # Delete source Volume
    def delete_volume(self, volume_id):
        print("Volume %s is being deleted..." % volume_id)
        response_delete_volume = self.ec2_client.delete_volume(VolumeId=volume_id)
        print("Volume %s deleted." % volume_id)

    # Create new TAG set for volume with EBS-CLONE as key
    def create_tag_specifications(self, local_source_volume_id, new_tag_value="EBS-CLONE-CREATED"):
        local_source_volume = self.ec2_resource.Volume(local_source_volume_id)

        local_new_tags = None

        # Add a new tag
        if local_source_volume.tags is not None and any(d['Key'] == 'EBS-CLONE' for d in local_source_volume.tags):
            local_new_tags = local_source_volume.tags
        else:
            local_new_tags = [{'Value': new_tag_value, 'Key': 'EBS-CLONE'}]
            if local_source_volume.tags:
                local_new_tags = local_source_volume.tags + local_new_tags

        return local_new_tags

    # Prepare and Copy source volume
    def prepare_and_copy_volume(self, source_device, target_device):
        output = None

        # Copy the volume (clone partitions)
        try:
            print("Start to copy device %s to %s ..." % (source_device, target_device))
            output = subprocess.check_output(["sudo", "dd", "bs=128M", "if=" + source_device, "of=" + target_device,
                                              "status=progress", "oflag=direct"])
            print(output)
            output = subprocess.check_output(["sync"])
            print(output)

        except subprocess.CalledProcessError as e:
            output = e.output
            print(output)
            self.rollback()
            exit(-1)

    # Start source instance
    def start_instance(self, instance_id):
        print("Source instance %s Starting..." % instance_id)
        self.ec2_client.start_instances(InstanceIds=[instance_id])
        stop_waiter = self.ec2_client.get_waiter('instance_running')
        stop_waiter.wait(InstanceIds=[instance_id])
        print("Source instance %s Started." % instance_id)

    # Rollback: recover snapshot and start instance
    def rollback(self):
        print("Rollback started...")

        source_volume = self.ec2_resource.Volume(self.source_volume_id)

        # Create new EBS volume from backup
        # newVolumeId = create_volume_from_existing_volume(volumeId = _source_volume_id, snapshotId = self._snapshot.id)

        # Detach and attach source_volume
        if self.source_volume_id:
            self.detach_volume(self.source_volume_id, self.local_instance_id)
            # Attach new EBS to original Instance
            self.attach_volume(attach_volume_id=self.source_volume_id,
                               attach_instance_id=self.source_instance_id,
                               attach_device=self.source_device)
            self.start_instance(self.source_instance_id)

        # Detach and delete generated volume
        if self.new_volume_id:
            self.detach_volume(self.new_volume_id, self.local_instance_id)
            self.delete_volume(self.new_volume_id)

        print("Rollback finished.")

        exit(-1)

    def run(self):
        # Retrieve AUX instance info
        local_instance_id = requests.get('http://169.254.169.254/latest/meta-data/instance-id').text

        # Stop source instance
        self.stop_instance(instance_id=self.source_instance_id)

        # Generate Snapshot for source volume
        self.snapshot(self.source_volume_id)

        # Detach source EBS from source Instance
        source_device = self.detach_volume(self.source_volume_id, self.source_instance_id)

        # Attach source EBS to AUX Instance
        self.attach_volume(self.source_volume_id, local_instance_id, self.aux_source_device)

        # Create new reduced EBS volume
        new_volume_id = self.create_volume_from_existing_volume(volume_id=self.source_volume_id)

        # Attach new EBS to AUX Instance
        self.attach_volume(new_volume_id, local_instance_id, self.aux_target_device)

        # Prepare and Copy source volume to new reduced one
        self.prepare_and_copy_volume(self.aux_source_device, self.aux_target_device)

        # Detach new EBS from AUX Instance
        self.detach_volume(new_volume_id, local_instance_id)

        # Attach new EBS to original Instance
        self.attach_volume(new_volume_id, self.source_instance_id, source_device)

        # Start source instance
        self.start_instance(self.source_instance_id)

        # Detach source EBS from AUX Instance
        self.detach_volume(self.source_volume_id, local_instance_id)

        # Delete source Volume
        self.delete_volume(self.source_volume_id)


# Inputs
@click.command()
@click.option('-si', '--instance-id', help='Source Instance Id. Ex: i-095c3fa3d1688eaa3 ')
@click.option('-sv', '--volume-id', help='Source Volume Id. Ex: vol-0a49b7a908e747385')
@click.option('-ns', '--new-size', help='New Size. Default will use the source size')
def main(**kwargs):
    source_instance_id = kwargs.pop('instance_id')
    source_volume_id = kwargs.pop('volume_id')
    new_size = kwargs.pop('new_size')

    decrypt_ebs = CloneEbsDecrypted(source_instance_id, source_volume_id, new_size)
    decrypt_ebs.run()


if __name__ == "__main__":
    main()
