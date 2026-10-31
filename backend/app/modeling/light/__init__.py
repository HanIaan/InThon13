"""Dasymetric light downscaling: NASA VNP46A3 pixels -> 200 m cells (L1).

Offline training lives in :mod:`app.modeling.light.train`; the service path imports only
:mod:`app.modeling.light.model`, which reads the versioned artifact and never touches
raw POI files.
"""
