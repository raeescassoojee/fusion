import pytest

from sentinel_camera_ai.aws_sink import AwsSink
from sentinel_camera_ai.config import AwsConfig


def test_aws_sink_refuses_disabled_configuration():
    with pytest.raises(ValueError, match="disabled"):
        AwsSink(AwsConfig(enabled=False))


def test_aws_sink_requires_bucket():
    with pytest.raises(ValueError, match="bucket"):
        AwsSink(AwsConfig(enabled=True, bucket=""))

