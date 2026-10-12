# TUI 的占位入口：当前只输出提示，没有连接 core 或绘制界面。

# 打印当前阶段尚未实现终端界面的说明。
def main() -> None:
    print("tui not implemented yet (S2)")


# 作为模块运行时调用 main；被其他代码导入时跳过此分支。
if __name__ == "__main__":
    main()
