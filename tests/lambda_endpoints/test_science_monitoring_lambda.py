"""Tests for the science data monitoring lambda."""

# # Validate that there is either and input or an output node for
# this instrument and descriptor
# # for every data_level.
# for level in self.data_levels:
#     node = DependencyNode(self.instrument, level, self.descriptor)
#     # This will raise an error if there is no ProcessingJob that produces this output.
#     try:
#         reader.get_node_for_output(node)
#     except ValueError:
#         nodes = reader.get_nodes_for_input(node)
#         if not nodes:
#             raise ValueError(f"No input or output node found for {self.instrument},
#             {level}, {self.descriptor}")
