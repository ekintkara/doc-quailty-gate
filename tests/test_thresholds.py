from app.config import ThresholdConfig, load_app_config, load_threshold_config


class TestDefaultThresholds:
    def test_defaults(self):
        config = load_threshold_config(config_dir="config")
        assert config.overall_threshold == 8.0
        assert config.critical_dimension_threshold == 6.0
        assert "correctness" in config.critical_dimensions
        assert "completeness" in config.critical_dimensions
        assert "implementability" in config.critical_dimensions

    def test_per_type_thresholds(self):
        config = load_threshold_config(config_dir="config", doc_type="refactor_plan")
        assert config.overall_threshold == 7.5

    def test_feature_spec_weights(self):
        config = load_threshold_config(config_dir="config", doc_type="feature_spec")
        assert config.dimension_weights["correctness"] == 1.5
        assert config.dimension_weights["clarity"] == 1.0

    def test_custom_type(self):
        config = load_threshold_config(config_dir="config", doc_type="custom")
        for w in config.dimension_weights.values():
            assert w == 1.0

    def test_migration_plan(self):
        config = load_threshold_config(config_dir="config", doc_type="migration_plan")
        assert config.dimension_weights["risk_awareness"] == 1.5

    def test_missing_config(self):
        config = load_threshold_config(config_dir="/nonexistent")
        assert config.overall_threshold == 8.0
        assert config.critical_dimension_threshold == 6.0


class TestAppConfig:
    def test_load_config(self):
        config = load_app_config(config_dir="config")
        assert config.proxy_base_url is not None
        assert config.model_aliases.get("critic_a") == "cheap_large_context"

    def test_model_aliases(self):
        config = load_app_config(config_dir="config")
        assert "critic_a" in config.model_aliases
        assert "critic_b" in config.model_aliases
        assert "validator" in config.model_aliases
        assert "scorer" in config.model_aliases


class TestThresholdConfig:
    def test_default_weights_keys(self):
        config = ThresholdConfig()
        expected_dims = [
            "correctness",
            "completeness",
            "implementability",
            "consistency",
            "edge_case_coverage",
            "testability",
            "risk_awareness",
            "clarity",
        ]
        for dim in expected_dims:
            assert dim in config.dimension_weights
