from units.convert import centimetres_to_metres


def metres_label(centimetres: float) -> str:
    return f"{centimetres_to_metres(centimetres):.2f} m"
