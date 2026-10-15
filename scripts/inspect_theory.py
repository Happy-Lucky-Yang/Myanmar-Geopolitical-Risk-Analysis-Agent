"""只读查看指定 Word 理论材料（不复制原文件、不生成索引）。"""
import argparse
import sys
from zipfile import ZipFile
from xml.etree import ElementTree as ET


def paragraphs(path):
    with ZipFile(path) as archive:
        data = archive.read('word/document.xml')
    if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
        raise ValueError('不支持包含实体定义的文档')
    root = ET.fromstring(data)
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    return [''.join(p.itertext()) for p in root.findall('.//w:p', ns)]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='gb18030' if sys.platform == 'win32' else 'utf-8', errors='backslashreplace')
    for text in paragraphs(args.path):
        if text.strip():
            print(text)
