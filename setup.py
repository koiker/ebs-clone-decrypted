from setuptools import setup

setup(
    name='ebs-clone-decrypted',
    version='0.1.0',
    packages=['ebs_clone_decrypted'],
    url='',
    license='Apache 2.0',
    author='Unknown',
    author_email='unknown',
    description='Script to convert an encrypted EBS to unencrypted',
    install_requires=[
        'boto3>=1.7.6',
        'botocore>=1.10.6',
        'click>=6.7'],
    entry_points={
        'console_scripts': [
            'decrypt_ebs = ebs_clone_decrypted.cli:main',
        ],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Environment :: Other Environment',
        'Intended Audience :: Developers',
        'Intended Audience :: Information Technology',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 3',
        'Topic :: Internet',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Utilities',
    ]
)
