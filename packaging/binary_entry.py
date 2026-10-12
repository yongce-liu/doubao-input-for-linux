"""独立冻结入口：不借用系统 dist-packages。"""
from doubao_input.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
