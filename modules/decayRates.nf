process decayRates {
    conda 'thesis_env.yml'

    input:
        path script
        path species
        path reactions
        path decay_file

    output:
        path "decay_rate_mapper.pkl", emit: decay_rate_mapper

    script:
    """
    python3 "${script}" --reactions "${reactions}" --species "${species}" --decay-file "${decay_file}"
    """
}