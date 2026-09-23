#!/usr/bin/env python3
"""Opt-in windowed IDE evidence: capture or press a key in one exact test window.

This is a qualification helper, not a product process controller. The caller
supplies a unique title from its disposable IDE instance; ambiguity refuses.
"""
import argparse
import ctypes as c
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--title', required=True)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--capture', type=Path)
    actions.add_argument('--key')
    actions.add_argument('--ready', action='store_true')
    args = parser.parse_args()
    x = c.CDLL('libX11.so.6')
    x.XOpenDisplay.restype = c.c_void_p
    display = x.XOpenDisplay(None)
    if not display:
        raise RuntimeError('windowed X11 display is required')
    x.XDefaultRootWindow.argtypes = [c.c_void_p]; x.XDefaultRootWindow.restype = c.c_ulong
    x.XQueryTree.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong), c.POINTER(c.c_ulong), c.POINTER(c.POINTER(c.c_ulong)), c.POINTER(c.c_uint)]
    x.XFetchName.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_void_p)]
    x.XFree.argtypes = [c.c_void_p]
    root = x.XDefaultRootWindow(display)
    matches, titles = [], []
    pending = [(root, 0)]
    while pending:
        window, depth = pending.pop()
        name = c.c_void_p()
        title = ''
        if x.XFetchName(display, window, c.byref(name)) and name.value:
            title = c.string_at(name).decode('utf-8', 'replace'); x.XFree(name)
        modern = subprocess.check_output(['xprop', '-id', str(window), '_NET_WM_NAME'], text=True)
        if ' = ' in modern:
            title = modern.split(' = ', 1)[1].strip().strip('"')
        if title:
            titles.append(title)
            if args.title in title and 'Map State: IsViewable' in subprocess.check_output(['xwininfo', '-id', str(window)], text=True):
                matches.append((window, title))
        if depth < 4:
            parent, returned_root, children, count = c.c_ulong(), c.c_ulong(), c.POINTER(c.c_ulong)(), c.c_uint()
            if x.XQueryTree(display, window, c.byref(returned_root), c.byref(parent), c.byref(children), c.byref(count)):
                pending.extend((children[number], depth + 1) for number in range(count.value))
                x.XFree(children)
    if len(matches) != 1:
        raise RuntimeError(f'expected one disposable IDE window, found {matches!r}; window titles: {titles!r}')
    window, title = matches[0]
    before = time.monotonic_ns()
    if args.capture:
        if args.capture.exists():
            raise FileExistsError(args.capture)
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'x11grab',
                        '-window_id', str(window), '-framerate', '1', '-i', os.environ['DISPLAY'],
                        '-frames:v', '1', '-threads', '1', str(args.capture)], check=True, timeout=20)
    elif args.key:
        x.XSetInputFocus.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_ulong]
        x.XSetInputFocus(display, window, 2, 0)
        x.XStringToKeysym.argtypes = [c.c_char_p]; x.XStringToKeysym.restype = c.c_ulong
        x.XKeysymToKeycode.argtypes = [c.c_void_p, c.c_ulong]; x.XKeysymToKeycode.restype = c.c_uint
        key = x.XKeysymToKeycode(display, x.XStringToKeysym(args.key.encode('ascii')))
        if not key:
            raise ValueError('unknown test key')
        xt = c.CDLL('libXtst.so.6'); xt.XTestFakeKeyEvent.argtypes = [c.c_void_p, c.c_uint, c.c_int, c.c_ulong]
        xt.XTestFakeKeyEvent(display, key, 1, 0); xt.XTestFakeKeyEvent(display, key, 0, 0)
        x.XSync.argtypes = [c.c_void_p, c.c_int]; x.XSync(display, 0)
    print(json.dumps({'window': window, 'title': title, 'before_ns': before, 'after_ns': time.monotonic_ns(),
                      'capture': None if args.capture is None else str(args.capture), 'key': args.key}))


if __name__ == '__main__':
    main()
