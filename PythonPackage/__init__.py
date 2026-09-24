"""
串口管理器模块
提供SerialManager类，用于管理串口通信和日志记录
"""

from .serial_manager import SerialManager
from  .Relay_library import Relay_library

__all__ = ['SerialManager','Relay_library']
__version__ = '1.0.0'