from setuptools import setup

APP = ['app.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': True,
    'plist': {
        'LSUIElement': False, # 取消隐藏 Dock 图标，作为正常应用运行以便请求权限
        'CFBundleName': 'IanChenClipboard',
        'CFBundleDisplayName': 'IanChenClipboard',
        'CFBundleIdentifier': 'com.custom.ianchenclipboard',
        'CFBundleVersion': '2.0.0',
        'CFBundleShortVersionString': '2.0.0',
        'NSAppleEventsUsageDescription': 'IanChenClipboard needs to monitor hotkeys',
    },
    'packages': ['PyQt6', 'pynput'],
    'includes': ['pynput.keyboard._darwin', 'pynput.mouse._darwin'],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)