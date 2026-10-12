process rateLaws {
    conda 'thesis_env.yml'

    input:
        path script
        path reactions
        path excel
        val  sheet

    output:
        path 'rate_law_mapper.pkl', emit: rate_law_mapper

    script:
    """
    python3 "${script}" --reactions "${reactions}" --excel "${excel}" --sheet "${sheet}"
    """
}