#!/bin/bash
cd "$(dirname "$0")"

# 清理旧进程
pkill -f "app.py"
pkill -f "MacClipboard"
pkill -f "IanChenClipboard"

if [ ! -d "venv" ]; then
    echo "创建 Python 虚拟环境..."
    python3 -m venv venv
fi

echo "激活虚拟环境..."
source venv/bin/activate

echo "升级 pip..."
pip install --upgrade pip

echo "安装依赖..."
pip install -r requirements.txt

echo "========================================="
echo "启动剪切板工具成功！"
echo "请在屏幕右上角点击 � 图标使用"
echo "点击历史记录后会自动粘贴到您当前的光标处！"
echo "========================================="

python app.py