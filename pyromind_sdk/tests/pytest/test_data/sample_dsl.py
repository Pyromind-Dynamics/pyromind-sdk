# workflow: Dataset Processing Test
a = CloneAndCacheDataset(dataset="openai/gsm8k")
b = DatasetConfigBuilderTextNode(user_prompt_field="question", assistant_response_field="answer")
c = PathJoinNode(base_path=a.dataset_path, subpath="main/train-00000-of-00001.parquet")
d = DatasetToJsonlNode(dataset_path=c.joined_path)
e = DatasetConfigBuilderNode(train_data_path=d.jsonl_path, dataset_kind_config=b.dataset_kind_config)
f = DatasetValidatorNode(dataset_config=e.dataset_config)
