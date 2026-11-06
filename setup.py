#!/usr/bin/env python3
from setuptools import setup, find_packages

setup(
    name="adorn",
    version="1.0.0",
    description="GNOME Customization Storefront & Desktop Tweaks",
    author="Grinch_",
    author_email="user.grinch@gmail.com",
    license="GPL-3.0-or-later",
    packages=find_packages(),
    package_data={
        "adorn": ["ui/*.css"],
    },
    data_files=[
        ("share/applications", ["data/com.grinch.gnome.adorn.desktop"]),
        ("share/metainfo", ["data/com.grinch.gnome.adorn.metainfo.xml"]),
        ("share/icons/hicolor/scalable/apps", ["data/icons/com.grinch.gnome.adorn.svg"]),
    ],
    entry_points={
        "console_scripts": [
            "adorn = adorn.main:main",
        ],
    },
    python_requires=">=3.10",
)
