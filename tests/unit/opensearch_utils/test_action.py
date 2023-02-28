import unittest

from sds_data_manager.lambda_code.SDSCode.opensearch_utils.action import Action


class TestAction(unittest.TestCase):
    """tests for action.py"""

    def test_validate_action_pass(self):
        """
        Tests that the validate_action method correctly determines
        that an object of type Action is of type Action and is returned
        unchanged
        """
        ## Arrange ##
        action_true = Action.CREATE

        ## Act ##
        action_out = Action.validate_action(action_true)

        ## Assert ##
        assert action_true == action_out

    def test_validate_action_fail(self):
        """
        Tests that the validate_action method correctly throws an error
        if the input is not of type Action.
        """
        ## Arrange ##
        action = "this is a string, not an action"

        ## Act / Assert ##
        self.assertRaises(TypeError, Action.validate_action, action)

    def test_is_action_true(self):
        """
        Tests that the is_action method correctly determines
        the input is type Action.
        """

        ## Arrange ##
        action = Action.CREATE
        action_true = True

        ## Act ##
        action_out = Action.is_action(action)

        ## Assert ##
        assert action_out == action_true

    def test_is_action_false(self):
        """
        Tests that the is_action method correctly determines that the
        input is not type Action.
        """

        ## Arrange ##
        action = "this is a string, not an action"
        action_true = False

        ## Act ##
        action_out = Action.is_action(action)

        ## Assert ##
        assert action_out == action_true
