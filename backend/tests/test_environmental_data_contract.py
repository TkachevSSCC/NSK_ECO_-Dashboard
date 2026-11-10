"""Contract tests for station JSON and Sigma environmental event payloads.

Run from backend with:
    .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime

from app.integrations.sigma import schema
from app.schemas.station import Station


class StationJsonContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.station = {
            "id": 17,
            "type_st": 0,
            "battery_life": 88.5,
            "latitude": 55.00812,
            "longitude": 82.93541,
            "PM_2_5": 18.4,
            "PM_10": 42.7,
            "overTLV": 0,
        }

    def test_station_response_has_required_environmental_fields(self) -> None:
        record = Station.model_validate(self.station)

        self.assertEqual(record.id, 17)
        self.assertIsInstance(record.PM_2_5, float)
        self.assertIsInstance(record.PM_10, float)
        self.assertIsInstance(record.latitude, float)
        self.assertIsInstance(record.longitude, float)
        self.assertGreaterEqual(record.latitude, -90)
        self.assertLessEqual(record.latitude, 90)
        self.assertGreaterEqual(record.longitude, -180)
        self.assertLessEqual(record.longitude, 180)

    def test_station_record_serializes_as_strict_json(self) -> None:
        payload = Station.model_validate(self.station).model_dump()

        encoded = json.dumps(payload, allow_nan=False)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["PM_2_5"], 18.4)
        self.assertEqual(decoded["PM_10"], 42.7)
        self.assertEqual(decoded["latitude"], 55.00812)
        self.assertEqual(decoded["longitude"], 82.93541)


class SigmaEventContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.station = {
            "id": 17,
            "type_st": 0,
            "battery_life": 88.5,
            "latitude": 55.00812,
            "longitude": 82.93541,
            "PM_2_5": 38.4,
            "PM_10": 61.2,
            "overTLV": True,
        }
        self.timestamp = "2026-11-10T12:34:56+00:00"

    def test_event_contains_measurements_coordinates_and_iso_timestamp(self) -> None:
        event = schema.station_event(
            "pollution.exceeded", self.station, timestamp=self.timestamp
        )
        payload = event["payload"]

        self.assertEqual(event["timestamp"], self.timestamp)
        self.assertEqual(datetime.fromisoformat(event["timestamp"]).utcoffset().total_seconds(), 0)
        self.assertEqual(payload["station"]["latitude"], 55.00812)
        self.assertEqual(payload["station"]["longitude"], 82.93541)
        self.assertEqual(payload["measurements"]["pm25"], 38.4)
        self.assertEqual(payload["measurements"]["pm10"], 61.2)
        self.assertEqual(payload["event_type"], "pollution.exceeded")

    def test_event_matches_required_sigma_json_schema(self) -> None:
        event = schema.station_event(
            "pollution.exceeded", self.station, timestamp=self.timestamp
        )
        payload = event["payload"]

        self.assertTrue(set(schema.SOURCE_SCHEMA["required"]).issubset(event))
        self.assertTrue(
            set(schema.SOURCE_SCHEMA["properties"]["payload"]["required"])
            .issubset(payload)
        )
        self.assertIsInstance(event["description"], str)
        self.assertIsInstance(event["timestamp"], str)
        self.assertIsInstance(payload["station"]["id"], int)
        self.assertIsInstance(payload["station"]["latitude"], (int, float))
        self.assertIsInstance(payload["station"]["longitude"], (int, float))
        self.assertIsInstance(payload["measurements"]["pm25"], (int, float))
        self.assertIsInstance(payload["measurements"]["pm10"], (int, float))

    def test_event_is_json_serializable_without_non_finite_values(self) -> None:
        event = schema.station_event(
            "pollution.exceeded", self.station, timestamp=self.timestamp
        )

        self.assertIsInstance(json.dumps(event, allow_nan=False), str)

    def test_non_finite_measurements_are_omitted(self) -> None:
        station = {**self.station, "PM_2_5": float("nan"), "PM_10": float("inf")}
        event = schema.station_event(
            "pollution.exceeded", station, timestamp=self.timestamp
        )
        measurements = event["payload"]["measurements"]

        self.assertNotIn("pm25", measurements)
        self.assertNotIn("pm10", measurements)
        json.dumps(event, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
