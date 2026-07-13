from importlib.metadata import entry_points

from lucy_cloud.exporter import CloudTraceExporter


def test_entry_point_exposes_cloud_exporter_factory():
    cloud = [
        point for point in entry_points(group="lucy.exporters") if point.name == "cloud"
    ]

    assert len(cloud) == 1
    factory = cloud[0].load()
    assert factory.__self__ is CloudTraceExporter
    assert factory.__name__ == "from_env"
