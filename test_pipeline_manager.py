import os
from manager import PipelineManager

def main():
    # Configuration for the test
    test_night = "2025-06-12"  # Example night
    test_objects = None  # Simulate all objects for the night
    config_path = "tides_pipe/config/config.yml"  # Path to the configuration file

    # Initialize the pipeline manager
    manager = PipelineManager(config_path=config_path)

    # Run the pipeline manager in test mode
    print(f"Starting pipeline manager for test night: {test_night}")
    manager.run(night=test_night, objects=test_objects)

if __name__ == "__main__":
    main()