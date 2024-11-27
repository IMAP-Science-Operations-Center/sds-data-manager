"""Test data dependency functions."""
# def test_get_downstream_dependencies(session):
#     "Tests get_downstream_dependencies function."
#     filename = "imap_hit_l1a_count-rates_20240101_v001.cdf"
#     file_params = ScienceFilePath.extract_filename_components(filename)

#     complete_dependents = get_downstream_dependencies(session, file_params)
#     expected_complete_dependent = {
#         "instrument": "hit",
#         "data_level": "l1b",
#         "descriptor": "all",
#         "version": "v001",
#         "start_date": "20240101",
#     }
#     assert len(complete_dependents) == 1

#     assert complete_dependents[0] == expected_complete_dependent
