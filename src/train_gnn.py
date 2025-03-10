import argparse
import numpy as np
import os
import random
import sys
import torch

from gnn.dataloading.build_graph import build_graph_from_df
from gnn.dataloading.loaders import load_datasets
from gnn.model.model import create_model
from gnn.model.trainer import Trainer
from gnn.utils.utils import get_vocab_size
from shared.global_constants import RES_DIR
from shared.loaders import load_train_val_nodes
from shared.utils import save_cli_options


def parse_arguments(args_to_parse):
    """ Parse CLI arguments """
    parser = argparse.ArgumentParser(description='Train a Text GCN model')

    general = parser.add_argument_group('General settings')
    general.add_argument('name', type=str, help='The name of the experimental directory - used for saving and loading.')
    general.add_argument(
        '--model', type=str, default='gcn', choices=['gcn', 'gat', 'spgat'], help='Select a GNN model to use'
    )
    general.add_argument(
        '--input-features',
        type=str,
        default='one-hot',
        choices=['one-hot', 'text2vec'],
        help='Select the type of input features to use. The `text2vec` option uses doc2vec and word2vec embeddings.',
    )
    general.add_argument(
        '--input-data-dir',
        type=str,
        required=True,
        help="The name of the directory in which we have a our processed corpora (in a DataFrame)",
    )
    general.add_argument(
        '--graph-data-dir',
        type=str,
        default='graph',
        help="The name of the directory in which we save the processed graph data - such as the adjacency and labels",
    )
    general.add_argument(
        '--train-dir',
        type=str,
        default='train',
        help="The name of the subdirectory where we should save training data (losses, metrics, models, etc.)",
    )
    general.add_argument(
        "--stemmer-path",
        type=str,
        required=True,
        help="Path to the SALAMA stemming dictionary",
    )
    general.add_argument(
        '--window-size',
        type=int,
        default=20,
        help='Select the number of tokens to use in the word co-occurence windows',
    )
    general.add_argument(
        '--wv-sg',
        type=int,
        default=1,
        choices=[0, 1],
        help='Select whether word2vec will use skip-gram (1) or CBOW (0)',
    )
    general.add_argument(
        '--pv-dm',
        type=int,
        default=1,
        choices=[0, 1],
        help='Select whether doc2vec will use DM (1) or DBOW (0)',
    )
    general.add_argument("--seed", type=int, default=12321, help='Random seed for reproducability')

    training = parser.add_argument_group('Training settings')
    training.add_argument(
        '--lr',
        type=float,
        default=0.02,
        help="Learning rate",
    )
    training.add_argument(
        '--dropout-ratio',
        type=float,
        default=0.5,
        help="Dropout rate to be applied to all layers",
    )
    training.add_argument(
        '--epochs',
        type=int,
        default=10,
        help="The number of epochs to run",
    )
    training.add_argument(
        '--train-set-label-proportion',
        type=float,
        default=0.2,
        choices=[0.01, 0.05, 0.1, 0.2],
        help='Ratio of nodes in the training set which we keep labelled',
    )
    training.add_argument(
        '--early-stopping-epochs',
        type=int,
        default=10,
        help="The number of epochs to stop after if there is no improvement in the metric of interest",
    )
    training.add_argument(
        '--disable-early-stopping',
        action='store_true',
        default=False,
        help="Whether to disable early stopping. Default is False",
    )
    training.add_argument(
        '--keep-all-checkpoints',
        action='store_true',
        default=False,
        help="Whether to keep all model checkpoints while training. \
            Default is False, in which case only the most recent checkpoint will be kept",
    )
    args = parser.parse_args(args_to_parse)

    # Append the window size to the train and graph directory names
    args.graph_data_dir = f"{args.graph_data_dir}_w{args.window_size}"
    args.train_dir = f"{args.train_dir}_w{args.window_size}_p{args.train_set_label_proportion}"
    return args


def main(args):
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    results_dir = os.path.join(RES_DIR, args.name)
    os.makedirs(results_dir, exist_ok=True)
    save_cli_options(args, results_dir)

    graph_dir = os.path.join(results_dir, args.graph_data_dir)

    if not os.path.isdir(graph_dir):
        print('Building graph...')
        os.makedirs(graph_dir, exist_ok=True)
        build_graph_from_df(
            graph_dir=graph_dir,
            df_path=os.path.join(RES_DIR, args.input_data_dir, 'dataset.csv'),
            stemming_map_path=os.path.join(RES_DIR, args.stemmer_path),
            input_feature_type=args.input_features,
            text_column='document_content',
            label_column='document_type',
            window_size=args.window_size,
            wv_sg=args.wv_sg,
            pv_dm=args.pv_dm,
        )

    print('Load and normalise...')
    adjacency, input_features, labels = load_datasets(graph_dir)

    train_nodes, val_nodes, test_nodes = load_train_val_nodes(
        preproc_dir=os.path.join(RES_DIR, args.input_data_dir),
        train_set_label_proportion=args.train_set_label_proportion,
    )

    # Initialise model, trainer, and train
    text_gcn_model = create_model(
        model_type=args.model,
        num_classes=len(labels.unique()),
        num_input_features=input_features.shape[1],
        num_hidden_features=200,
        num_heads=8,
        dropout_ratio=args.dropout_ratio,
        use_bias=False,
        relu_negative_slope=0.2,
    )
    trainer = Trainer(
        model=text_gcn_model,
        learning_rate=args.lr,
        device='cpu',
        train_nodes=train_nodes,
        val_nodes=val_nodes,
        test_nodes=test_nodes,
        vocab_size=get_vocab_size(graph_dir),
        results_dir=os.path.join(results_dir, args.train_dir),
        validate_every_n_epochs=2,
        save_after_n_epochs=0,
        checkpoint_every_n_epochs=2,
        use_early_stopping=not args.disable_early_stopping,
        early_stopping_epochs=args.early_stopping_epochs,
        autodelete_checkpoints=not args.keep_all_checkpoints,
    )
    print('Training...')
    trainer(
        input_features=input_features,
        adjacency=adjacency,
        labels=labels,
        num_epochs=args.epochs,
    )
    trainer.save_test_metrics(
        input_features=input_features,
        adjacency=adjacency,
        labels=labels,
    )
    print('Complete!')


if __name__ == '__main__':
    args = parse_arguments(sys.argv[1:])
    main(args)
