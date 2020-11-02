def tabulate(data, *, as_list=False, codeblock=False, language="prolog"):
    """Create a pretty codeblock table

    Uses hljs's prolog syntax highlighting

    Recommended hljs languages (for codeblocks):
    - prolog (default)
    - asciidoc

    Parameters
    -----------
    data: :class:`List[List[name, value]]
        The data to turn into a table
    as_list: Optional[:class:`bool`]
        Whether to return a list of strings. Overrides codeblock
    codeblock: Optional[:class:`bool`]
        Whether to return the table in a codeblock
    language: Optional[:class:`str`]
        The hljs language to use for syntax highlighting
    """
    # Go though the data and find the longest name
    longest_name = 0

    for name, value in data:
        name_len = len(name)
        if name_len > longest_name:
            longest_name = name_len

    # Format the data, using the longest name as a reference
    # for adding on spaces to other names
    table = []

    for name, value in data:
        # Add on extra spaces if needed
        to_add = "".join(" " for i in range(longest_name - len(name)))

        table.append(f"{name}{to_add} :: {value}")

    if as_list:
        return table

    final_table = "\n".join(table)

    # Append a codeblock if specified
    if codeblock:
        final_table = f"```{language}\n{final_table}\n```"

    return final_table
