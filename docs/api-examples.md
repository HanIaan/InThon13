# API 응답 예시

> 아래 응답은 Mock 모드(`DATA_MODE=mock`) 서버에서 실제로 받은 응답입니다. 실데이터 모드에서도 **구조는 동일**하며 `data_status`·`data_mode` 값과 수치만 달라집니다.
> 다시 생성하기: `backend/`에서 서버를 띄우고 같은 URL을 호출하세요.

### health

`GET /api/v1/health` → **200**

```json
{
  "status": "ok",
  "api_version": "v1",
  "data_mode": "mock",
  "database": {
    "status": "disabled",
    "message": "mock mode: database not used"
  },
  "data_ready": true,
  "server_time": "2026-10-31T17:05:00.666954+09:00"
}
```

### grid (features 2개만 표시)

`GET /api/v1/grid?bbox=126.975%2C37.565%2C126.985%2C37.570&time_slot=night&sound_sensitivity=5&light_sensitivity=3&crowd_sensitivity=1` → **200**

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": "SEL200-0098-0089",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              126.974605,
              37.564393
            ],
            [
              126.976869,
              37.564403
            ],
            [
              126.976857,
              37.566206
            ],
            [
              126.974592,
              37.566196
            ],
            [
              126.974605,
              37.564393
            ]
          ]
        ]
      },
      "properties": {
        "grid_id": "SEL200-0098-0089",
        "comfort_score": 14.7,
        "grade": "C",
        "noise_score": 19.7,
        "light_score": 8.6,
        "crowd_score": 7.6,
        "confidence": 0.65,
        "data_status": "mock"
      }
    },
    {
      "type": "Feature",
      "id": "SEL200-0099-0089",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              126.976869,
              37.564403
            ],
            [
              126.979134,
              37.564413
            ],
            [
              126.979121,
              37.566216
            ],
            [
              126.976857,
              37.566206
            ],
            [
              126.976869,
              37.564403
            ]
          ]
        ]
      },
      "properties": {
        "grid_id": "SEL200-0099-0089",
        "comfort_score": 12.7,
        "grade": "C",
        "noise_score": 18.0,
        "light_score": 3.9,
        "crowd_score": 12.5,
        "confidence": 0.71,
        "data_status": "mock"
      }
    }
  ],
  "meta": {
    "api_version": "v1",
    "data_mode": "mock",
    "time_slot": "night",
    "sensitivities": {
      "sound_sensitivity": 5,
      "light_sensitivity": 3,
      "crowd_sensitivity": 1
    },
    "weights": {
      "noise": 0.556,
      "light": 0.333,
      "crowd": 0.111
    },
    "generated_at": "2026-10-31T17:05:00.673923+09:00",
    "bbox": [
      126.975,
      37.565,
      126.985,
      37.57
    ],
    "cell_size_m": 200,
    "cell_count": 20
  }
}
```

### location/inspect (nearby_sensors 1개만 표시)

`GET /api/v1/location/inspect?lat=37.5665&lng=126.978&time_slot=night&sound_sensitivity=5&light_sensitivity=3&crowd_sensitivity=1` → **200**

```json
{
  "query": {
    "sound_sensitivity": 5,
    "light_sensitivity": 3,
    "crowd_sensitivity": 1,
    "lat": 37.5665,
    "lng": 126.978,
    "time_slot": "night"
  },
  "grid_id": "SEL200-0099-0090",
  "grid_center": [
    126.977983,
    37.567112
  ],
  "grid_geometry": {
    "type": "Polygon",
    "coordinates": [
      [
        [
          126.976857,
          37.566206
        ],
        [
          126.979121,
          37.566216
        ],
        [
          126.979109,
          37.568018
        ],
        [
          126.976844,
          37.568008
        ],
        [
          126.976857,
          37.566206
        ]
      ]
    ]
  },
  "comfort_score": 13.5,
  "grade": "C",
  "confidence": 0.59,
  "data_status": "mock",
  "weights": {
    "noise": 0.556,
    "light": 0.333,
    "crowd": 0.111
  },
  "indicators": {
    "noise": {
      "indicator": "noise",
      "score": 16.3,
      "raw_value": 69.3,
      "raw_unit": "dB",
      "status": "mock",
      "source": "sdot",
      "source_name": "서울 S-DoT 도시데이터 센서",
      "observed_at": "2026-10-02T01:00:00+09:00",
      "period_start": "2026-10-01T22:00:00+09:00",
      "period_end": "2026-10-02T02:00:00+09:00",
      "method": "mock",
      "nearby_sensors": [
        {
          "sensor_id": "MOCK-0099-0090-2",
          "source": "sdot",
          "name": "Mock sensor 3",
          "coordinates": [
            126.977642,
            37.565408
          ],
          "distance_m": 125.2,
          "raw_value": 68.9,
          "raw_unit": "dB",
          "observed_at": "2026-10-02T01:00:00+09:00"
        }
      ],
      "note": "데모용 합성 데이터입니다. 실제 측정값이 아닙니다."
    },
    "light": {
      "indicator": "light",
      "score": 10.7,
      "raw_value": 61.1,
      "raw_unit": "lux",
      "status": "mock",
      "source": "sdot",
      "source_name": "서울 S-DoT 도시데이터 센서",
      "observed_at": "2026-10-02T01:00:00+09:00",
      "period_start": "2026-10-01T22:00:00+09:00",
      "period_end": "2026-10-02T02:00:00+09:00",
      "method": "mock",
      "nearby_sensors": [
        {
          "sensor_id": "MOCK-0099-0090-0",
          "source": "sdot",
          "name": "Mock sensor 1",
          "coordinates": [
            126.976399,
            37.566185
          ],
          "distance_m": 145.6,
          "raw_value": 64.5,
          "raw_unit": "lux",
          "observed_at": "2026-10-02T01:00:00+09:00"
        }
      ],
      "note": "데모용 합성 데이터입니다. 실제 측정값이 아닙니다."
    },
    "crowd": {
      "indicator": "crowd",
      "score": 7.9,
      "raw_value": 43422.0,
      "raw_unit": "persons/km2",
      "status": "mock",
      "source": "living_population",
      "source_name": "서울 생활인구",
      "observed_at": "2026-10-02T01:00:00+09:00",
      "period_start": "2026-10-01T22:00:00+09:00",
      "period_end": "2026-10-02T02:00:00+09:00",
      "method": "mock",
      "nearby_sensors": [],
      "note": "데모용 합성 데이터입니다. 실제 측정값이 아닙니다."
    }
  },
  "notes": [
    "건물 단위 실측값이 아니라 좌표 주변 200m 격자의 환경 추정값입니다.",
    "Mock 모드: 모든 값은 데모용 합성 데이터입니다."
  ],
  "meta": {
    "api_version": "v1",
    "data_mode": "mock",
    "time_slot": "night",
    "sensitivities": {
      "sound_sensitivity": 5,
      "light_sensitivity": 3,
      "crowd_sensitivity": 1
    },
    "weights": {
      "noise": 0.556,
      "light": 0.333,
      "crowd": 0.111
    },
    "generated_at": "2026-10-31T17:05:00.678603+09:00"
  }
}
```

### location/inspect — 낮 시간대 (빛 지표 not_applicable)

`GET /api/v1/location/inspect?lat=37.5665&lng=126.978&time_slot=day&sound_sensitivity=3&light_sensitivity=3&crowd_sensitivity=3` → **200**

```json
{
  "comfort_score": 0.0,
  "weights": {
    "noise": 0.5,
    "light": 0.0,
    "crowd": 0.5
  },
  "indicators": {
    "light": {
      "indicator": "light",
      "score": null,
      "raw_value": null,
      "raw_unit": null,
      "status": "not_applicable",
      "source": null,
      "source_name": null,
      "observed_at": null,
      "period_start": null,
      "period_end": null,
      "method": null,
      "nearby_sensors": [],
      "note": "낮 시간대에는 빛공해 지표를 적용하지 않습니다."
    }
  }
}
```

### recommendations (center+radius, 2개)

`GET /api/v1/recommendations?mode=rest&lat=37.5665&lng=126.978&radius_m=1000&limit=2&time_slot=night&sound_sensitivity=5&light_sensitivity=3&crowd_sensitivity=1` → **200**

```json
{
  "items": [
    {
      "rank": 1,
      "type": "grid",
      "grid_id": "SEL200-0095-0088",
      "name": null,
      "center": [
        126.96895,
        37.563466
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              126.967824,
              37.56256
            ],
            [
              126.970088,
              37.56257
            ],
            [
              126.970075,
              37.564373
            ],
            [
              126.967811,
              37.564363
            ],
            [
              126.967824,
              37.56256
            ]
          ]
        ]
      },
      "comfort_score": 25.9,
      "grade": "C",
      "noise_score": 35.1,
      "light_score": 14.9,
      "crowd_score": 13.1,
      "confidence": 0.75,
      "data_status": "mock",
      "distance_m": 867.2
    },
    {
      "rank": 2,
      "type": "grid",
      "grid_id": "SEL200-0095-0089",
      "name": null,
      "center": [
        126.968937,
        37.565269
      ],
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [
              126.967811,
              37.564363
            ],
            [
              126.970075,
              37.564373
            ],
            [
              126.970063,
              37.566175
            ],
            [
              126.967798,
              37.566165
            ],
            [
              126.967811,
              37.564363
            ]
          ]
        ]
      },
      "comfort_score": 25.2,
      "grade": "C",
      "noise_score": 37.4,
      "light_score": 7.5,
      "crowd_score": 17.0,
      "confidence": 0.7,
      "data_status": "mock",
      "distance_m": 812.0
    }
  ],
  "meta": {
    "api_version": "v1",
    "data_mode": "mock",
    "time_slot": "night",
    "sensitivities": {
      "sound_sensitivity": 5,
      "light_sensitivity": 3,
      "crowd_sensitivity": 1
    },
    "weights": {
      "noise": 0.556,
      "light": 0.333,
      "crowd": 0.111
    },
    "generated_at": "2026-10-31T17:05:00.689569+09:00",
    "mode": "rest",
    "search": {
      "center": [
        126.978,
        37.5665
      ],
      "radius_m": 1000.0
    },
    "candidate_count": 73,
    "poi_available": false
  }
}
```

### data/status

`GET /api/v1/data/status` → **200**

```json
{
  "data_mode": "mock",
  "sources": [
    {
      "source_id": "sdot",
      "name": "서울 S-DoT 도시데이터 센서 (mock)",
      "status": "mock",
      "last_updated_at": "2026-10-01T00:00:00+09:00",
      "coverage": {
        "bbox": [
          126.76,
          37.41,
          127.19,
          37.72
        ],
        "description": "서울 전역 (합성 데모 데이터)",
        "temporal_start": "2026-10-01T00:00:00+09:00",
        "temporal_end": "2026-10-02T00:00:00+09:00"
      },
      "observation_count": 0,
      "missing_rate": 0.03,
      "spatial_resolution": "point sensors",
      "update_frequency": "hourly",
      "notes": "Mock 모드: 실제 S-DoT 데이터가 아닙니다."
    },
    {
      "source_id": "living_population",
      "name": "서울 생활인구 (mock)",
      "status": "mock",
      "last_updated_at": "2026-10-01T00:00:00+09:00",
      "coverage": {
        "bbox": [
          126.76,
          37.41,
          127.19,
          37.72
        ],
        "description": "서울 전역 (합성 데모 데이터)",
        "temporal_start": "2026-10-01T00:00:00+09:00",
        "temporal_end": "2026-10-02T00:00:00+09:00"
      },
      "observation_count": 0,
      "missing_rate": 0.03,
      "spatial_resolution": "행정동",
      "update_frequency": "daily",
      "notes": "Mock 모드: 실제 생활인구 데이터가 아닙니다."
    },
    {
      "source_id": "nasa_black_marble",
      "name": "NASA Black Marble VNP46 (mock)",
      "status": "mock",
      "last_updated_at": "2026-10-01T00:00:00+09:00",
      "coverage": {
        "bbox": [
          126.76,
          37.41,
          127.19,
          37.72
        ],
        "description": "서울 전역 (합성 데모 데이터)",
        "temporal_start": "2026-10-01T00:00:00+09:00",
        "temporal_end": "2026-10-02T00:00:00+09:00"
      },
      "observation_count": 0,
      "missing_rate": 0.03,
      "spatial_resolution": "15 arc-second (~460 m)",
      "update_frequency": "daily / monthly",
      "notes": "Mock 모드: 빛공해는 S-DoT 조도만 합성 사용합니다."
    }
  ],
  "grid": {
    "crs": "EPSG:5179",
    "cell_size_m": 200,
    "cell_count": 33408,
    "precomputed_slots": [
      "day",
      "evening",
      "night",
      "dawn"
    ]
  },
  "generated_at": "2026-10-31T17:05:00.693953+09:00"
}
```

### 오류: bbox_too_large

`GET /api/v1/grid?bbox=126.76%2C37.41%2C127.19%2C37.72&time_slot=day` → **400**

```json
{
  "error": {
    "code": "bbox_too_large",
    "message": "bbox covers 33408 grid cells; the limit is 6000. Zoom in.",
    "details": {
      "cell_count": 33408,
      "max_cells": 6000
    }
  }
}
```

### 오류: validation_error

`GET /api/v1/grid?bbox=126.97%2C37.55%2C127.00%2C37.58&time_slot=noon` → **422**

```json
{
  "error": {
    "code": "validation_error",
    "message": "request parameters failed validation",
    "details": [
      {
        "loc": [
          "query",
          "time_slot"
        ],
        "msg": "Input should be 'day', 'evening', 'night' or 'dawn'",
        "type": "enum"
      }
    ]
  }
}
```

### 오류: out_of_coverage

`GET /api/v1/location/inspect?lat=35.1&lng=129.0&time_slot=day` → **404**

```json
{
  "error": {
    "code": "out_of_coverage",
    "message": "coordinate is outside the Seoul service area",
    "details": {
      "lat": 35.1,
      "lng": 129.0
    }
  }
}
```

