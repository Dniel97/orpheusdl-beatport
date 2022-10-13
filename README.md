<!-- PROJECT INTRO -->

OrpheusDL - Beatport
=================

A Beatport module for the OrpheusDL modular archival music program

[Report Bug](https://github.com/Dniel97/orpheusdl-beatport/issues)
·
[Request Feature](https://github.com/Dniel97/orpheusdl-beatport/issues)


## Table of content

- [About OrpheusDL - Beatport](#about-orpheusdl-beatport)
- [Getting Started](#getting-started)
    - [Prerequisites](#prerequisites)
    - [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
    - [Global](#global)
    - [Beatport](#beatport)
- [Contact](#contact)



<!-- ABOUT ORPHEUS -->
## About OrpheusDL - Beatport

OrpheusDL - Beatport is a module written in Python which allows archiving from **Beatport** for the modular music archival program.


<!-- GETTING STARTED -->
## Getting Started

Follow these steps to get a local copy of Orpheus up and running:

### Prerequisites

* Already have [OrpheusDL](https://github.com/yarrm80s/orpheusdl) installed

### Installation

1. Go to your `orpheusdl/` directory and run the following command:
   ```sh
   git clone https://github.com/Dniel97/orpheusdl-beatport.git modules/beatport
   ```
2. Execute:
   ```sh
   python orpheus.py
   ```
3. Now the `config/settings.json` file should be updated with the Beatport settings

<!-- USAGE EXAMPLES -->
## Usage

Just call `orpheus.py` with any link you want to archive:

```sh
python orpheus.py https://www.beatport.com/track/darkside/10844269
```

<!-- CONFIGURATION -->
## Configuration

You can customize every module from Orpheus individually and also set general/global settings which are active in every
loaded module. You'll find the configuration file here: `config/settings.json`

### Global

```json5
"global": {
    "general": {
        // ...
        "download_quality": "high"
    },
    "covers": {
        "main_resolution": 1400,
        // ...
    },
    // ...
}
```

`download_quality`: Choose one of the following settings:
* "hifi": same as lossless
* "lossless": same as high
* "high": AAC 256 kbit/s
* "medium": same as low
* "low": same as minimum
* "minimum": AAC 128 kbit/s

`main_resolution`: Beatport supports resolutions from 100x100px to 1400x1400px max.
A value greater than `1400` is clamped at `1400` so that the cover is not scaled up.

### Beatport
```json
{
    "username": "",
    "password": ""
}
```

| Option   | Info                                            |
|----------|-------------------------------------------------|
| username | Enter your Beatport email/username address here |
| password | Enter your Beatport password here               |

**NOTE: You need an active "LINK" subscription to use this module. "Professional", formerly known as "LINK Pro" is
required to get  AAC 256 kbit/s?**

<!-- Contact -->
## Contact

Yarrm80s (pronounced 'Yeargh mateys!') - [@yarrm80s](https://github.com/yarrm80s)

Dniel97 - [@Dniel97](https://github.com/Dniel97)

Project Link: [OrpheusDL Beatport Public GitHub Repository](https://github.com/Dniel97/orpheusdl-beatport)
