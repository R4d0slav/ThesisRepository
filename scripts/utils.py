#-------------------------------------------------------------------------------------#
                                    # Imports #
#-------------------------------------------------------------------------------------#
import  re
import  math
import  json
import  dill
import  warnings
import  statistics

import  numpy               as np
import  pandas              as pd
import  seaborn             as sns
import  networkx            as nx
import  tellurium           as te
import  plotly.express      as px
import  matplotlib.pyplot   as plt

from    bs4                 import BeautifulSoup
from    pathlib             import Path
from    matplotlib.ticker   import MaxNLocator

#-------------------------------------------------------------------------------------#
                                    # Constants #
#-------------------------------------------------------------------------------------#
# Dynamic
PSS_FILE_NAME = 'output-laws-RISC_ignored_10112025.sbml'                                # Original PSS sbml/xml file
# PSS_FILE_NAME = 'output_only_RISC_ignored_21102025.sbml'                                # Original PSS sbml/xml file

# Static
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # Adjust if file is nested
DATA_PATH = PROJECT_ROOT / "data"
OUTPUT_PATH = PROJECT_ROOT / "results"

PSS_NETWORK_PATH = DATA_PATH / PSS_FILE_NAME


PSS_REACTIONS_PATH = OUTPUT_PATH / "processPSS" / "reactions.json"         # PSS reactions
PSS_SPECIES_PATH = OUTPUT_PATH / "processPSS" / "species.json"             # PSS species

ATH_DECAY_RATES = DATA_PATH / "ath_decay_rates.tsv"            # Decay Rates used for creating a mapper function
PSS_DECAY_RATE_MAPPER = OUTPUT_PATH / "decayRates" / "decay_rate_mapper.pkl"   # Decay Rate Mapper function

INITIAL_CONCENTRATIONS = DATA_PATH / "Initial_Concentrations.csv"
PSS_INITIAL_CONCENTRATION_MAPPER = OUTPUT_PATH / "initialConcentrations" / "concentration_mapper.pkl"   # Initial Concentration Mapper function

REACTIONS_BY_COMBINATION_PATH = DATA_PATH / "reactions_by_combination.xlsx"
REACTIONS_BY_COMBINATION_SHEET = "reactions_by_combination_v5"
REACTIONS_BY_COMBINATION_SHEET_MAS = "reactions_by_combination_v5_Mas"
KINETIC_RATE_LAWS_PATH = OUTPUT_PATH / "rateLaws" / "rate_law_mapper.pkl"  # Rate Law Mapper function
KINETIC_RATE_LAWS_PATH_MAS = OUTPUT_PATH / "rateLaws" / "kinetic_rate_laws_mas.pkl"  # Rate Law Mapper function

KINETIC_PARAMETERS_PATH = DATA_PATH / "Reactions_kinetics.xlsx"
KINETIC_NIB_PARAMETERS_PATH = DATA_PATH / "reactions-2025.02.04.xlsx"
KINETIC_NIB_PARAMETERS_SHEET = 'reactions-29-09-2025'
PSS_KINETIC_PARAMETER_MAPPER = OUTPUT_PATH / "kineticParameters" / "kinetic_parameter_mapper.pkl"   # Kinetic Parameter Mapper function

# ANTIMONY_PATH = PROJECT_ROOT / "data_" / "processed" / "Antimony.txt"
ANTIMONY_PATH = OUTPUT_PATH / "buildAntimony" / "Antimony.txt"

RED = "\033[91m"
RESET = "\033[0m"

sns.set(style="whitegrid")
warnings.simplefilter(action='ignore', category=pd.errors.PerformanceWarning)

#-------------------------------------------------------------------------------------#
                                    # Functions #
#-------------------------------------------------------------------------------------#

