process initialConcentrations {
    conda 'thesis_env.yml'

    input:
        path script
        path species
        path concentrations

    output:
        path "initial_concentration_mapper.pkl", emit: concentration_mapper

    script:
    """
    python3 "${script}" --species "${species}" --concentrations "${concentrations}"
    """
}