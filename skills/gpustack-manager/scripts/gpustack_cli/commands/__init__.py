"""各子命令实现。

每个模块自带 register(): 把它的 argparse 组装和 handler 放在同一文件里，
这样新增一个域只改一个文件，cli.py 只维护注册顺序。
"""
