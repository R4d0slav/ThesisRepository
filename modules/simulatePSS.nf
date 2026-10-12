process simulatePSS {
    conda 'thesis_env.yml'
    errorStrategy 'ignore'

    input:
        path script
        path antimony
        val sim_params

    output:
        path "images/*.jpg",       emit: plots
        path "tables/*.csv",       emit: tables
        path "interactive/*.html", emit: htmls
    
    script:
    def event_arg = sim_params.events ? "--events ${sim_params.events}" : ""

    // Optional CVODE integrator overrides — global params, applied to every condition.
    def tol_args = ""
    if (params.rel_tol   != null) tol_args += " --rel_tol ${params.rel_tol}"
    if (params.abs_tol   != null) tol_args += " --abs_tol ${params.abs_tol}"
    if (params.max_steps != null) tol_args += " --max_steps ${params.max_steps}"

    """
    python3 "${script}" --antimony "${antimony}" --start ${sim_params.start} --end ${sim_params.end} --points ${sim_params.points} --suffix ${sim_params.suffix} ${event_arg}${tol_args}
    """
}