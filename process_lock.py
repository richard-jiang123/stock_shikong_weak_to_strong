#!/usr/bin/env python3
"""
进程锁机制：防止多实例同时运行关键任务

使用文件锁实现，支持跨进程同步。
"""
import os
import fcntl
import time
from contextlib import contextmanager

LOCK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.locks')

def _ensure_lock_dir():
    """确保锁目录存在"""
    if not os.path.exists(LOCK_DIR):
        os.makedirs(LOCK_DIR, exist_ok=True)

@contextmanager
def file_lock(lock_name, timeout=30, blocking=True):
    """
    文件锁上下文管理器

    Args:
        lock_name: 锁名称（如 'daily_run', 'batch_update'）
        timeout: 等待超时秒数
        blocking: 是否阻塞等待，False时立即返回是否获取成功

    Usage:
        with file_lock('daily_run'):
            # 执行需要锁保护的操作
            ...

    Returns:
        如果 blocking=False，返回 (acquired, lock_file)
        如果 blocking=True，阻塞直到获取锁或超时

    Raises:
        TimeoutError: 如果超时仍未获取锁
    """
    _ensure_lock_dir()
    lock_path = os.path.join(LOCK_DIR, f'{lock_name}.lock')

    lock_file = open(lock_path, 'w')

    acquired = False
    try:
        if blocking:
            # 阻塞等待，带超时
            start_time = time.time()
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (IOError, OSError):
                    if time.time() - start_time >= timeout:
                        raise TimeoutError(f"等待锁 '{lock_name}' 超时 ({timeout}s)")
                    time.sleep(0.5)
        else:
            # 非阻塞，立即返回
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (IOError, OSError):
                acquired = False

        if blocking or acquired:
            # 写入锁定信息
            lock_file.write(f"{os.getpid()}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            lock_file.flush()

            yield lock_file
        else:
            yield None

    finally:
        if acquired:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()

def is_locked(lock_name):
    """
    检查某个锁是否被占用

    Returns:
        bool: True表示锁被占用
    """
    _ensure_lock_dir()
    lock_path = os.path.join(LOCK_DIR, f'{lock_name}.lock')

    if not os.path.exists(lock_path):
        return False

    try:
        with open(lock_path, 'r') as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                # 能获取锁说明没有被占用
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                return False
            except (IOError, OSError):
                # 无法获取说明被占用
                return True
    except Exception:
        return False

def get_lock_info(lock_name):
    """
    获取锁的持有者信息

    Returns:
        dict: {'pid': 进程ID, 'time': 锁定时间} 或 None
    """
    _ensure_lock_dir()
    lock_path = os.path.join(LOCK_DIR, f'{lock_name}.lock')

    if not os.path.exists(lock_path):
        return None

    try:
        with open(lock_path, 'r') as f:
            lines = f.readlines()
            if len(lines) >= 2:
                return {
                    'pid': int(lines[0].strip()),
                    'time': lines[1].strip()
                }
    except Exception:
        pass
    return None


if __name__ == '__main__':
    import argparse
    import sys

    parser = argparse.ArgumentParser(description='进程锁管理')
    parser.add_argument('--acquire', metavar='NAME', help='获取锁')
    parser.add_argument('--release', metavar='NAME', help='释放锁')
    parser.add_argument('--timeout', type=int, default=30, help='等待超时秒数')
    parser.add_argument('--status', metavar='NAME', help='检查锁状态')
    args = parser.parse_args()

    if args.acquire:
        try:
            with file_lock(args.acquire, timeout=args.timeout):
                print('LOCK_ACQUIRED')
                sys.stdout.flush()
                # 用 sleep loop 保持进程存活，锁不释放
                # 父进程用 kill 终止时，OS 自动释放 fcntl.flock
                while True:
                    time.sleep(1)
        except TimeoutError:
            print('LOCK_TIMEOUT')
            sys.exit(1)

    elif args.release:
        # 释放锁：检查并清理锁文件
        lock_path = os.path.join(LOCK_DIR, f'{args.release}.lock')
        if os.path.exists(lock_path):
            if not is_locked(args.release):
                os.remove(lock_path)
                print('LOCK_RELEASED')
            else:
                print('LOCK_STILL_HELD')
        else:
            print('LOCK_NOT_FOUND')

    elif args.status:
        locked = is_locked(args.status)
        info = get_lock_info(args.status)
        print(f'locked={locked}')
        if info:
            print(f'pid={info["pid"]}')
            print(f'time={info["time"]}')

    else:
        parser.print_help()