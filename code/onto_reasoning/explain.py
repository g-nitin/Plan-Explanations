import rdflib
import argparse
from os import path as os_path
from sys import path as sys_path
import logging

# Need to add the parent directory to the sys path to import the rdf_utils & intent_utils module
#   More info: https://sentry.io/answers/import-files-from-a-different-folder-in-python/
sys_path.insert(1, "/".join(os_path.realpath(__file__).split("/")[0:-2]))
from templates.rdf_utils import (extract_actions, get_grounded_predicates,
                                 get_preconditions_from_rdf, get_effects_from_rdf)
from templates.intent_utils import get_intent


def load_ontology(file_path):
    """
    Load the ontology from the given file path.

    @param file_path: The path to the ontology file
    @type file_path: str
    @return: The loaded RDF graph
    @rtype: rdflib.Graph
    """
    g = rdflib.Graph()
    g.parse(file_path, format="xml")
    return g


def get_action_template(g, action):
    """
    Get the NL template for the given action.

    @param g: The RDF graph containing the ontology
    @type g: rdflib.Graph
    @param action: The action name and parameters
    @type action: str
    @return: The NL template for the action
    @rtype: str or None
    """
    action_str = f"https://purl.org/ai4s/ontology/planning#{action.split()[0]}"
    logger.info(f"Querying for action: {action_str}")

    query = f"""
    PREFIX planning: <https://purl.org/ai4s/ontology/planning#>
    SELECT ?actionTemplate
    WHERE {{
        ?action a planning:DomainAction ;
                planning:hasNLTemplate ?actionTemplate .
        FILTER(str(?action) = '{action_str}')
    }}
    """

    results = g.query(query)
    logger.info(f"Query results: {list(results)}")

    return next(iter(results), [None])[0]


def get_predicate_templates(g, action, predicate_type):
    """
    Get the NL templates for preconditions or effects of the given action.

    @param g: The RDF graph containing the ontology
    @type g: rdflib.Graph
    @param action: The action name
    @type action: str
    @param predicate_type: Either "Precondition" or "Effect"
    @type predicate_type: str
    @return: A list of NL templates for the predicates
    @rtype: List[str]
    """
    action_str = f"https://purl.org/ai4s/ontology/planning#{action}"
    query = f"""
    PREFIX planning: <https://purl.org/ai4s/ontology/planning#>
    SELECT ?predicateTemplate
    WHERE {{
        ?action a planning:DomainAction ;
                planning:has{predicate_type} ?predicate .
        ?predicate planning:hasNLTemplate ?predicateTemplate .
        FILTER(str(?action) = '{action_str}')
    }}
    """
    results = g.query(query)
    return [str(row[0]) for row in results]


def replace_placeholders(template, mapping):
    """
    Replace placeholders in the template with grounded values.

    @param template: The NL template with placeholders
    @type template: str
    @param mapping: A dictionary mapping lifted predicates to grounded predicates
    @type mapping: Dict[str, str]
    @return: The template with placeholders replaced by grounded values
    @rtype: str
    """
    for lifted, grounded in mapping.items():
        for placeholder, value in zip(lifted.split()[1:], grounded.split()[1:]):
            template = template.replace(placeholder, f"'{value}'")
    return template


def generate_explanation(g, action, in_plan):
    """
    Generate an explanation for why an action is used or not used in the plan.

    @param g: The RDF graph containing the ontology
    @type g: rdflib.Graph
    @param action: The action name and parameters
    @type action: str
    @param in_plan: Whether the action is in the plan or not
    @type in_plan: bool
    @return: An explanation string
    @rtype: str
    """
    action_name = action.split()[0]
    action_template = get_action_template(g, action)
    precondition_templates = get_predicate_templates(g, action_name, "Precondition")
    effect_templates = get_predicate_templates(g, action_name, "Effect")

    grounded_preconditions, grounded_effects = get_grounded_predicates(action, g)
    lifted_preconditions = get_preconditions_from_rdf(g, action_name.lower())
    lifted_effects = get_effects_from_rdf(g, action_name.lower())

    precondition_mapping = dict(zip(lifted_preconditions, grounded_preconditions))
    effect_mapping = dict(zip(lifted_effects, grounded_effects))

    explanation = f"Action: {replace_placeholders(action_template, precondition_mapping)}\n"
    explanation += "Preconditions:\n" + "\n".join(
        f"- {replace_placeholders(p, precondition_mapping)}" for p in precondition_templates)
    explanation += "\nEffects:\n" + "\n".join(f"- {replace_placeholders(e, effect_mapping)}" for e in effect_templates)

    if in_plan:
        explanation += ("\nThis action is used in the plan because its preconditions are met "
                        "and its effects are necessary for achieving the goal.")
    else:
        explanation += ("\nThis action is not used in the plan because either its preconditions are not met "
                        "or its effects are not necessary for achieving the goal.")

    return explanation


def compare_actions(g, action1, action2):
    """
    Compare two actions and explain why one is used instead of the other.

    @param g: The RDF graph containing the ontology
    @type g: rdflib.Graph
    @param action1: The first action name and parameters
    @type action1: str
    @param action2: The second action name and parameters
    @type action2: str
    @return: A comparison explanation string
    @rtype: str
    """
    explanation1 = generate_explanation(g, action1, True)
    explanation2 = generate_explanation(g, action2, False)

    comparison = f"Comparison between {action1} and {action2}:\n\n"
    comparison += f"{action1}:\n{explanation1}\n\n"
    comparison += f"{action2}:\n{explanation2}\n\n"
    comparison += (f"The plan uses {action1} instead of {action2} because "
                   f"it better fits the current state and goal of the problem.")

    return comparison


def main(plan_file, question):
    """
    Main function to process the plan file and question, and generate an explanation.

    @param plan_file: Path to the plan file
    @type plan_file: str
    @param question: The question about the plan
    @type question: str
    """
    ontology_file = "../../data/sokoban/plan-ontology-rdf-instances_sokoban.owl"
    logger.info(f"Loading ontology from {ontology_file}")

    g = load_ontology(ontology_file)
    logger.info(f"Ontology loaded successfully with {len(g)} triples.")

    intent, intent_num = get_intent(question)
    logger.info(f"Detected intent: {intent}")

    actions = extract_actions(question)
    logger.info(f"Extracted actions: {actions}")

    # print_all_action_templates(g)

    with open(plan_file, 'r') as f:
        plan = [line.strip() for line in f.readlines()]

    if intent_num == 1:  # Why is action A not used in the plan?
        action = actions[0]
        explanation = generate_explanation(g, action, False)
        print(explanation)

    elif intent_num == 2:  # Why is action A used in the plan?
        action = actions[0]
        if any(action in step for step in plan):
            explanation = generate_explanation(g, action, True)
            print(explanation)
        else:
            print(f"The action {action} is not used in the given plan.")

    elif intent_num == 3:  # Why is action A used rather than action B?
        action1, action2 = actions
        comparison = compare_actions(g, action1, action2)
        print(comparison)

    else:
        print("Invalid question type.")


if __name__ == "__main__":
    # logging.basicConfig(level=logging.INFO)  # TO ENABLE LOGGING
    logging.basicConfig(level=logging.CRITICAL)  # TO DISABLE LOGGING
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description="Process a plan file and answer a question.")
    parser.add_argument("plan_file", type=str, help="The path to the plan file")
    parser.add_argument("question", type=str, help="The question to answer")

    args = parser.parse_args()

    main(args.plan_file, args.question)
