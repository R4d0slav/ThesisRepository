process kineticParameters {
    conda 'thesis_env.yml'

    input:
        path script
        path species
        path reactions
        path gathered
        path nib
        val nib_sheet

    output:
        path "kinetic_parameter_mapper.pkl", emit: kinetics_mapper

    script:
    """
    python3 "${script}" --reactions "${reactions}" --species "${species}" --gathered "${gathered}" --nib "${nib}" --nib-sheet "${nib_sheet}"
    """
}