UNCLASSIFIED = "unclassified"


def category_owners(topics, categories):
    if not isinstance(topics, dict):
        raise ValueError("topics must map automatic group names to centroid vectors.")
    if not isinstance(categories, dict):
        raise ValueError("categories must map category names to lists of automatic groups.")
    owners = {}
    for name, groups in categories.items():
        if not isinstance(name, str) or not name.strip() or name == UNCLASSIFIED:
            raise ValueError("Each category must have a nonempty, nonreserved name.")
        if not isinstance(groups, list):
            raise ValueError("Each category must contain a list of automatic group names.")
        for group in groups:
            if not isinstance(group, str) or group not in topics:
                raise ValueError("Each assigned group must exist in topics.")
            if group in owners:
                raise ValueError("An automatic group can belong to only one category.")
            owners[group] = name
    return owners
