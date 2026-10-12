#!/usr/bin/env nextflow

/*═════════════════════════════════════════════════════════════════════════════╗
║              PSS KINETIC MODELLING PIPELINE  •  Nextflow DSL2                ║
║                                                                              ║
║  Constructs and simulates an Antimony kinetic model from a PSS network,      ║
║  applying decay rates, initial concentrations, rate laws, and kinetic        ║
║  parameters before running multi-condition stress simulations.               ║
╚═════════════════════════════════════════════════════════════════════════════*/

include { processPSS }              from './modules/processPSS.nf'
include { decayRates }              from './modules/decayRates.nf'
include { initialConcentrations }   from './modules/initialConcentrations.nf'
include { rateLaws }                from './modules/rateLaws.nf'
include { kineticParameters }       from './modules/kineticParameters.nf'
include { buildAntimony }           from './modules/buildAntimony.nf'
include { simulatePSS }             from './modules/simulatePSS.nf'

workflow {
    main:
        """───────────────────────────────────────────────────────────────────┐
        │  STAGE 1 — Parse PSS Network                                        │
        │  Reads the SBML file and extracts structured reactions/species maps │
        └───────────────────────────────────────────────────────────────────"""
        processPSS(
            file("${params.script_path}/process_pss.py"),
            file("${params.data_path}/output-laws-RISC_ignored_10112025.sbml")
        )

        """───────────────────────────────────────────────────────────────────┐
        │  STAGE 2 — Build Parameter Mappers                                  │
        │  All four mappers run in parallel, each consuming the outputs of    │
        │  Stage 1 alongside their respective input data files.               │
        └───────────────────────────────────────────────────────────────────"""

        """ 2a. Decay rates — maps each species to its mRNA/protein turnover rate """
        decayRates(
            file("${params.script_path}/decay_rates.py"),
            processPSS.out.species,
            processPSS.out.reactions,
            file("${params.data_path}/ath_decay_rates.tsv")
        )

        """ 2b. Initial concentrations — steady-state starting values per species """
        initialConcentrations(
            file("${params.script_path}/concentrations.py"),
            processPSS.out.species,
            file("${params.data_path}/Initial_Concentrations.csv")
        )

        """ 2c. Rate laws — assigns kinetic law equations to each reaction class """
        rateLaws(
            file("${params.script_path}/rate_laws.py"),
            processPSS.out.reactions,
            file("${params.data_path}/reactions_by_combination.xlsx"),
            "reactions_by_combination_v5"               // sheet name
        )

        """2d. Kinetic parameters — Vmax, Km, and related constants per reaction
                Draws from two source files: a catalysis table and an NIB table"""
        kineticParameters(
            file("${params.script_path}/kinetic_parameters.py"),
            processPSS.out.species,
            processPSS.out.reactions,
            file("${params.data_path}/Reactions_kinetics.xlsx"),
            file("${params.data_path}/reactions-2025.02.04.xlsx"),
            "reactions-29-09-2025_2"                 // sheet name
        )

        """───────────────────────────────────────────────────────────────────┐
        │  STAGE 3 — Assemble Antimony Model                                  │
        │  Merges all mappers with the network topology to produce a single   │
        │  Antimony-format ODE model ready for simulation.                    │
        └───────────────────────────────────────────────────────────────────"""
        buildAntimony(
            file("${params.script_path}/build_antimony.py"),
            processPSS.out.species,
            processPSS.out.reactions,
            rateLaws.out.rate_law_mapper,
            kineticParameters.out.kinetics_mapper,
            decayRates.out.decay_rate_mapper,
            initialConcentrations.out.concentration_mapper
        )

        """───────────────────────────────────────────────────────────────────┐
        │  STAGE 4 — Run Simulations                                          │
        │                                                                     │
        │  Each entry in sim_params defines one simulation condition:         │
        │    • start / end / points  →  time-course integration settings      │
        │    • suffix                →  label appended to all output files    │
        │    • events (optional)     →  JSON file of perturbation events      │
        │                                                                     │
        │  Conditions run in parallel; add/remove rows freely below.          │
        └───────────────────────────────────────────────────────────────────"""
        // channel.from([
        //     [start:0, end:1_000, points:1_000, suffix:'default'],
        //     [start:0, end:1_000, points:1_000, suffix:'drought',          events: file("${params.events_path}/intervention/drought.json")],
        //     [start:0, end:1_000, points:1_000, suffix:'waterlogging',     events: file("${params.events_path}/intervention/waterlogging.json")],
        //     [start:0, end:1_000, points:1_000, suffix:'heat',             events: file("${params.events_path}/intervention/heat.json")],
            // [start:0, end:1_000, points:1_000, suffix:'trichousbacteria', events: file("${params.events_path}/intervention/trichousbacteria.json")],
            // [start:0, end:1_000, points:1_000, suffix:'aba',              events: file("${params.events_path}/intervention/aba.json")],
            // [start:0, end:10_000, points:30_000, suffix:'ja',               events: file("${params.events_path}/intervention/ja.json")],
            // [start:0, end:1_000, points:1_000, suffix:'sa',               events: file("${params.events_path}/intervention/sa.json")],
        // ]).set { sim_params }

        if (params.sim_conditions) {
            // Read from JSON file provided by the GUI
            channel.fromPath(params.sim_conditions)
                .splitJson()
                .map { cond ->
                    [
                        start: cond.start,
                        end:   cond.end,
                        points: cond.points,
                        suffix: cond.suffix,
                        events: cond.events ? file(cond.events) : null
                    ]
                }
                .set { sim_params }
        } else {
            // Fallback to default list (the one currently in the file)
            channel.from([
                [start:0, end:1_000, points:1_000, suffix:'default'],
                [start:0, end:1_000, points:1_000, suffix:'drought',      events: file("${params.events_path}/intervention/drought.json")],
                [start:0, end:1_000, points:1_000, suffix:'waterlogging', events: file("${params.events_path}/intervention/waterlogging.json")],
                [start:0, end:1_000, points:1_000, suffix:'heat',         events: file("${params.events_path}/intervention/heat.json")],
                [start:0, end:1_000, points:1_000, suffix:'coi1',         events: file("${params.events_path}/knockout/coi1.json")],
            ]).set { sim_params }
        }


        simulatePSS(
            file("${params.script_path}/simulate.py"),
            buildAntimony.out.antimony,
            sim_params
        )
    
    """───────────────────────────────────────────────────────────────────┐
    │  PUBLISH — Declare outputs for Nextflow's output block below        │
    └───────────────────────────────────────────────────────────────────"""
    publish:
        pss_reactions           = processPSS.out.reactions
        pss_species             = processPSS.out.species
        decay_rates             = decayRates.out
        initial_concentrations  = initialConcentrations.out
        rate_laws               = rateLaws.out
        kinetic_parameters      = kineticParameters.out
        antimony                = buildAntimony.out
        simulation_plots        = simulatePSS.out.plots
        simulation_tables       = simulatePSS.out.tables
        simulation_htmls        = simulatePSS.out.htmls
}

/*══════════════════════════════════════════════════════════════════════════════
 OUTPUT DIRECTORY ROUTING                                                      
 Each output group is written into a subdirectory named after its process.     
══════════════════════════════════════════════════════════════════════════════*/
output {
    pss_reactions           { path { "${processPSS.name}" } }
    pss_species             { path { "${processPSS.name}" } }

    decay_rates             { path { "${decayRates.name}" } }
    initial_concentrations  { path { "${initialConcentrations.name}" } }
    rate_laws               { path { "${rateLaws.name}" } }
    kinetic_parameters      { path { "${kineticParameters.name}" } }

    antimony                { path { "${buildAntimony.name}" } }
    
    simulation_plots        { path { "${simulatePSS.name}" } }
    simulation_tables       { path { "${simulatePSS.name}" } }
    simulation_htmls        { path { "${simulatePSS.name}" } }
}