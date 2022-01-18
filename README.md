# BAGLE: Bayesian Analysis of Gravitational Lensing Events

BAGLE allows modeling of gravitational microlensing events both photometrically and astrometrically. Supported microlensing models include:

- PSPL: point-source, point-lens with parallax
- PSBL: point-source, binary-lens
- FSPL: finite-source, point-lens (minimal support... not well tested yet)
     
All models support fitting data with single or multi-band photometry only, astrometry only, or joint fitting of photometry and astrometry (recommended). 

## Installation Instructions
### Clone the repo: 
     git clone https://github.com/ninjab3381/BAGLE_Microlensing.git

### Install required modules:
     Before you can execute the tests, you will need to install the following modules:
      - pip3 install matplotlib
      - pip3 install numpy
      - pip3 install astropy
      - pip3 install pytest
      - pip3 install celerite
      - pip3 install ephem
      - pip3 install pymultinest
      - pip3 install dynesty
      
      The above commands can also be executed as:
      - python3 -m pip install <module name>

### Run tests
     Navigate to the BAGLE_Microlensing folder:
     $ cd BAGLE_Microlensing/      
      
     Then run the tests using the following commands in the BAGLE_Microlensing folder:
      - python3 -m pytest tests
      
      or you can run the testing scripts individually:
      
      - python3 -m pytest tests/test_model.py
      - python3 -m pytest tests/test_model_fitter.py
      - python3 -m pytest tests/testingmodels.py
