from ruamel.yaml import YAML

from vigia import publicas
from vigia.config import parse_config
from vigia.publicas import PublicCamera, config_yaml, find_live, is_live, list_caltrans


def cctv(name, place, stream, in_service="true"):
    return {
        "cctv": {
            "inService": in_service,
            "location": {"locationName": name, "nearbyPlace": place},
            "imageData": {"streamingVideoURL": stream, "static": {"currentImageURL": f"https://img/{place}.jpg"}},
        }
    }


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self.responses[url]


def test_list_caltrans_filters_and_searches():
    url = publicas.JSON_URL.format(d=4)
    payload = {
        "data": [
            cctv("TV1 -- I-80 : Ashby", "Emeryville", "https://v/1.m3u8"),
            cctv("TV2 -- US-101 : Candlestick", "San Francisco", "https://v/2.m3u8"),
            cctv("TV3 -- I-880", "Oakland", ""),  # sin video
            cctv("TV4 -- I-580", "Oakland", "https://v/4.m3u8", in_service="false"),
        ]
    }
    session = FakeSession({url: FakeResponse(payload=payload)})
    cams = list_caltrans(4, session=session)
    assert [c.place for c in cams] == ["Emeryville", "San Francisco"]
    assert cams[0].id == "tv1_i_80_ashby" and cams[0].image == "https://img/Emeryville.jpg"
    assert [c.place for c in list_caltrans(4, search="san francisco", session=session)] == ["San Francisco"]


def test_is_live():
    session = FakeSession(
        {
            "https://ok.m3u8": FakeResponse(text="#EXTM3U\n#EXT-X-VERSION:3"),
            "https://caida.m3u8": FakeResponse(status=404),
            "https://rara.m3u8": FakeResponse(text="<html>"),
        }
    )
    assert is_live("https://ok.m3u8", session=session)
    assert not is_live("https://caida.m3u8", session=session)
    assert not is_live("https://rara.m3u8", session=session)


def test_find_live_keeps_list_order(monkeypatch):
    cams = [PublicCamera(f"c{i}", f"Cam {i}", "X", 4, f"https://v/{i}.m3u8") for i in range(10)]
    monkeypatch.setattr(publicas, "list_caltrans", lambda district, search=None: cams)
    monkeypatch.setattr(publicas, "is_live", lambda url, timeout=8.0, session=None: url.endswith(("3.m3u8", "5.m3u8", "8.m3u8")))
    assert [c.id for c in find_live(4, count=2)] in (["c3", "c5"], ["c3", "c8"], ["c5", "c8"])
    assert [c.id for c in find_live(4, count=5)] == ["c3", "c5", "c8"]


def test_generated_config_is_valid(tmp_path):
    cams = [
        PublicCamera("tv1", 'TV1 -- I-80 "Ashby"', "Emeryville", 4, "https://v/1.m3u8"),
        PublicCamera("tv1", "TV1 -- I-80 (otra)", "Emeryville", 4, "https://v/1b.m3u8"),
    ]
    cfg = parse_config(YAML(typ="safe").load(config_yaml(cams, port=9000)), base_dir=tmp_path)
    assert [c.id for c in cfg.cameras] == ["tv1", "tv1_2"]
    assert cfg.cameras[0].name == 'TV1 -- I-80 "Ashby" (Emeryville)'
    assert cfg.web.port == 9000 and cfg.modes.default == "transito"
    rules = {r.name: r for r in cfg.modes.definitions["transito"].rules}
    assert rules["peaton_en_autopista"].title == "Peatón en la autopista"
    assert rules["congestion"].params["categorias"] == ["vehiculo"]
    assert any("banquina" in w for w in cfg.warnings)  # se dibuja desde el panel
