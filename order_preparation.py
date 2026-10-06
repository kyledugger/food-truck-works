"""Selected preparation choices, without prices or unselected catalog options."""
def modifiers(item):
    result = []
    for variant in item.get("selectedVariants") or []:
        for group in variant.get("selectableVariations") or []:
            values = [str(value.get("name") or "") for value in group.get("values") or [] if value.get("name")]
            if values:
                result.append({"attribute": str(group.get("attribute") or "Options"), "values": values})
        for variation in variant.get("variations") or []:
            if variation.get("value"):
                result.append({"attribute": str(variation.get("attribute") or "Options"), "values": [str(variation["value"])]})
    return result
