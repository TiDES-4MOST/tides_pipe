# __main__.py
import logging
import argparse
from tides_pipe.manager import PipelineManager

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="Run the pipeline manager.")
    parser.add_argument("--config", type=str, help="Path to the configuration file")
    parser.add_argument("--modules", nargs="*", help="List of modules to run")
    parser.add_argument("--night", help="Night of observations to process")
    parser.add_argument("--objects", nargs="*", help="List of objects to process (default is to find them automatically)")
    args = parser.parse_args()

    config_path = args.config if args.config else "config/config.yml"
    manager = PipelineManager(modules=args.modules, config_path=config_path)
    manager.run(night=args.night, objects=args.objects)

if __name__ == "__main__":
    main()
