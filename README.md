# Tides Pipe

This repository contains the Tides Pipe project, a backend pipeline for ingesting, processing and classifying astronomical spectra from the TiDES survey of 4MOST. Follow the instructions below to set up the project, configure the pipeline, and run the classification modules.

---

## Table of Contents

- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Modules](#modules)
  - [Data Ingestion](#data-ingestion)
  - [Classification](#classification)
  - [Database Update](#database-update)
- [Logging](#logging)
- [Contributing](#contributing)
- [License](#license)

---

## Installation

1. Clone the repository:
    ```sh
    git clone https://github.com/wisemanp/tides_pipe.git
    cd tides_pipe
    ```

2. Install the required dependencies:
    ```sh
    pip install -r requirements.txt
    ```

---

## Configuration

The pipeline is configured using a YAML file. The default configuration file is located at `config/config.yml`. You can specify a different configuration file using the `--config` argument when running the pipeline.

Example configuration:
```yaml
base_dir: "/path/to/base"
deliveries_dir: "/path/to/base/deliveries"
spectra_dir: "/path/to/base/spectra"
archive_dir: "/path/to/base/archive"
log_dir: "/path/to/base/logs"
modules:
  - data_ingestion
  - classification
  - db_update
classification:
  codes:
    - code: "snid"
      config_file: "/path/to/snid/config.yml"
    - code: "ngsf"
      config_file: "/path/to/ngsf/config.yml"
    - code: "siren"
      config_file: "/path/to/siren/config.yml"
```  
---

## Example: Running the NGSF Classification Module
To use the NGSF part of the classification module in tides_pipe, follow these steps:

1. Prepare the Configuration File:
Create a YAML configuration file (e.g., ngsf_config.yaml) with the necessary parameters for the NGSF classification. Below is an example configuration file:
```yaml
bank_dir: /path/to/bank_dir
output_dir: /path/to/output_dir
Alam_high: 2.0
Alam_low: -2.0
Alam_interval: 0.2
epoch_low: 0
```
Save this file in a location accessible to your script.

2. Prepare the Spectrum File:
Ensure you have the spectrum file (e.g., spectrum.fits) that you want to classify. Place it in an accessible directory.

3. Run the NGSF Classification:
Use the following Python script to run the NGSF classification module:
```python 
from tides_pipe.modules.classifiers.ngsf_handler import NGSFHandler

# Path to the configuration file
config_file = "/path/to/ngsf_config.yaml"

# Path to the spectrum file
spectrum_path = "/path/to/spectrum.fits"

# Initialize the NGSFHandler
handler = NGSFHandler(config_file=config_file)

# Run the classification
result = handler.classify(spectrum_path=spectrum_path)

# Print the result
print(result)
```
4. Output:
The result of the classification will be printed to the console. For example:
```python
{
    "result": "ngsf_classified",
    "spectrum": "/path/to/spectrum.fits"
}
```
Notes
- Ensure that all required dependencies are installed before running the pipeline.
- The NGSF classification module requires a properly configured YAML file and valid spectrum files.
- For additional details, refer to the documentation or contact the project maintainers.