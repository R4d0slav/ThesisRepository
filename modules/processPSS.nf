process processPSS {
    conda 'thesis_env.yml'

    input:
        path script
        path pss_file
    
    output:
        path "reactions.json", emit: reactions
        path "species.json",   emit: species
    
    script:
    """
    python3 "${script}" --input "${pss_file}"
    """
}