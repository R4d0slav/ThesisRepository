process buildAntimony {
    conda 'thesis_env.yml'

    input:
        path script
        path species
        path reactions
        path rate_law_mapper
        path kinetics_mapper
        path decay_rate_mapper
        path concentration_mapper

    output:
        path 'Antimony.txt', emit: antimony

    script:
    """
    python3 "${script}" --reactions "${reactions}" --species "${species}" --decay "${decay_rate_mapper}" --conc "${concentration_mapper}" --rate-laws "${rate_law_mapper}" --kinetics "${kinetics_mapper}" --subset ${params.subset} --decay-form ${params.decay_form}
    """
}