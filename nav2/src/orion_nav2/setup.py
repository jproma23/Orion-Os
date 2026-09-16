from setuptools import setup
import os
from glob import glob

package_name = 'orion_nav2'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='jproma23',
    description='Ponte ORION <-> Nav2',
    license='MIT',
    entry_points={
        'console_scripts': [
            'bridge_scan_odom = orion_nav2.bridge_scan_odom:main',
            'bridge_cmd_vel = orion_nav2.bridge_cmd_vel:main',
            'bridge_goal = orion_nav2.bridge_goal:main',
        ],
    },
)
