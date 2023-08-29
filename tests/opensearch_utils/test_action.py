import pytest

from sds_data_manager.lambda_code.SDSCode.opensearch_utils.action import Action


def test_validate_action_pass():
    """
    Tests that the validate_action method correctly determines
    that an object of type Action is of type Action and is returned
    unchanged
    """
    ## Arrange ##
    action_expected = Action.CREATE

    ## Act ##
    action_out = Action.validate_action(action_expected)

    ## Assert ##
    assert action_expected == action_out


def test_validate_action_fail():
    """
    Tests that the validate_action method correctly throws an error
    if the input is not of type Action.
    """
    ## Arrange ##
    action = "this is a string, not an action"

    ## Act / Assert ##
    with pytest.raises(TypeError):
        Action.validate_action(action)


def test_is_action_expected():
    """
    Tests that the is_action method correctly determines
    the input is type Action.
    """

    ## Arrange ##
    action = Action.CREATE
    action_expected = True

    ## Act ##
    action_out = Action.is_action(action)

    ## Assert ##
    assert action_out == action_expected


def test_is_action_false():
    """
    Tests that the is_action method correctly determines that the
    input is not type Action.
    """

    ## Arrange ##
    action = "this is a string, not an action"
    action_expected = False

    ## Act ##
    action_out = Action.is_action(action)

    ## Assert ##
    assert action_out == action_expected
