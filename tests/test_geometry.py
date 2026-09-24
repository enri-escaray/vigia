from vigia.geometry import Line, Zone, iou, point_in_polygon, segments_intersect


def test_point_in_polygon():
    square = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert point_in_polygon((0.5, 0.5), square)
    assert not point_in_polygon((1.5, 0.5), square)
    concave = [(0, 0), (4, 0), (4, 4), (2, 1), (0, 4)]
    assert point_in_polygon((1, 0.5), concave)
    assert not point_in_polygon((2, 3), concave)


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert abs(iou((0, 0, 10, 10), (5, 0, 15, 10)) - 1 / 3) < 1e-9


def test_segments_intersect():
    assert segments_intersect((0, 0), (1, 1), (0, 1), (1, 0))
    assert not segments_intersect((0, 0), (1, 0), (0, 1), (1, 1))
    assert segments_intersect((0, 0), (1, 0), (1, 0), (2, 5))  # se tocan en un extremo


def test_line_crossing_direction():
    # Línea vertical dibujada de abajo hacia arriba: la flecha de entrada apunta a la derecha.
    line = Line("acceso", (0.35, 0.95), (0.35, 0.05))
    assert line.crossing((0.2, 0.5), (0.5, 0.5)) == 1  # izquierda -> derecha = entrada
    assert line.crossing((0.5, 0.5), (0.2, 0.5)) == -1  # derecha -> izquierda = salida
    assert line.crossing((0.2, 0.5), (0.3, 0.6)) == 0  # no la cruza
    assert line.crossing((0.2, 0.99), (0.5, 0.99)) == 0  # pasa por fuera del segmento


def test_zone_to_pixels_and_dict():
    zone = Zone("z", ((0.1, 0.2), (0.5, 0.2), (0.5, 0.6)))
    assert zone.to_pixels(100, 50) == [(10, 10), (50, 10), (50, 30)]
    assert zone.to_dict() == {"nombre": "z", "puntos": [[0.1, 0.2], [0.5, 0.2], [0.5, 0.6]]}
