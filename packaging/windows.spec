# Bundle only application resources. Configuration and working data stay next to the executable.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_delvewheel_libs_directory, copy_metadata


root = Path(SPECPATH).resolve().parent
icon = root / "assets" / "wildberries-analytics.ico"
report_extensions = {".json", ".pbip", ".pbir", ".pbism", ".tmdl"}
report_files = [
    path
    for path in (root / "powerbi").rglob("*")
    if path.is_file() and ".pbi" not in path.parts
    and (path.suffix in report_extensions or path.name == ".platform")
]
resources = [icon, *sorted((root / "sql").glob("[0-9][0-9][0-9]_*.sql")), *sorted(report_files)]
datas = [(str(path), str(path.parent.relative_to(root))) for path in resources]
datas += collect_data_files("tzdata")
for dependency in ("psycopg", "psycopg-binary", "tzdata"):
    datas += copy_metadata(dependency)
datas, binaries = collect_delvewheel_libs_directory("psycopg_binary", datas=datas)

analysis = Analysis(
    [str(root / "src" / "windows_app.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["psycopg_binary"],
)
archive = PYZ(analysis.pure)
application = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="Wildberries Analytics",
    console=False,
    icon=str(icon),
    upx=False,
)
