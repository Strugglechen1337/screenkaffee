if __name__ == "__main__":
    try:
        from screenkaffee.__main__ import main
        raise SystemExit(main())
    except Exception:
        import os
        from pathlib import Path
        import sys
        import traceback
        root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        folder = Path(os.environ.get("SCREENKAFFEE_DATA", root / "data"))
        if "--data-dir" in sys.argv:
            index = sys.argv.index("--data-dir")
            if index + 1 < len(sys.argv):
                folder = Path(sys.argv[index + 1])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        if "--self-test" in sys.argv or "--diagnose" in sys.argv:
            raise SystemExit(1)
        raise
