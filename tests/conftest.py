import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from automesh.config import Config  # noqa: E402

STEP_TEXT = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION((''),'2;1');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));
ENDSEC;
DATA;
#10=( LENGTH_UNIT ( ) NAMED_UNIT ( * ) SI_UNIT ( .MILLI., .METRE. ) );
#11=CARTESIAN_POINT('',(0.,0.,0.));
#12=CARTESIAN_POINT('',(200.,80.,40.));
#13=CARTESIAN_POINT('',(-20.,5.,2.5));
#14=CIRCLE('',#20,1.5);
#15=CYLINDRICAL_SURFACE('',#21,6.0);
#16=ADVANCED_FACE('',(#30),#15,.T.);
#17=ADVANCED_FACE('',(#31),#18,.T.);
#18=PLANE('',#22);
#19=EDGE_CURVE('',#40,#41,#14,.T.);
#23=CLOSED_SHELL('',(#16,#17));
#24=MANIFOLD_SOLID_BREP('',#23);
ENDSEC;
END-ISO-10303-21;
"""


def _binary_stl(triangles):
    data = b"AutoMesh test STL".ljust(80, b"\0")
    data += struct.pack("<I", len(triangles))
    for a, b, c in triangles:
        data += struct.pack("<12fH", 0.0, 0.0, 1.0,
                            a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2], 0)
    return data


def _cube_triangles(size=1.0):
    s = size
    v = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0),
         (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    quads = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    tris = []
    for a, b, c, d in quads:
        tris.append((v[a], v[b], v[c]))
        tris.append((v[a], v[c], v[d]))
    return tris


@pytest.fixture
def step_file(tmp_path):
    path = tmp_path / "part.step"
    path.write_text(STEP_TEXT, encoding="utf-8")
    return str(path)


@pytest.fixture
def stl_file(tmp_path):
    path = tmp_path / "cube.stl"
    path.write_bytes(_binary_stl(_cube_triangles(0.05)))
    return str(path)


@pytest.fixture
def cfg(tmp_path):
    config = Config()
    config.fluent.use_mock = True
    config.output.run_root = str(tmp_path / "runs")
    return config


@pytest.fixture(autouse=True)
def _isolate_user_paths_file(tmp_path_factory, monkeypatch):
    """Kullanıcı geneli yol kaydı testlerde gerçek profile yazmasın."""
    from automesh import launcher

    target = tmp_path_factory.mktemp("automesh-yollar") / "yollar.txt"
    monkeypatch.setattr(launcher, "USER_PATHS_FILE", str(target))
