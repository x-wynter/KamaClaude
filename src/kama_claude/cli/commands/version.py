# 版本子命令：从顶层包读取 __version__，用内置 print 写入标准输出。

import kama_claude


# 打印当前 kama_claude 包的版本号
def cmd_version() -> None:
    print(kama_claude.__version__)
