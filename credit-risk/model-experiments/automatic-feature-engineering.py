import argparse
import joblib
import os
import re
import subprocess
from typing import List, Tuple
import itertools
import pandas as pd
import numpy as np
import featuretools as ft
from featuretools.primitives import AggregationPrimitive, TransformPrimitive
from featuretools_tsfresh_primitives import (
    comprehensive_fc_parameters,
    primitives_from_fc_settings,
)
from featuretools_tsfresh_primitives import primitives as tsfresh_primitives
from featuretools_tsfresh_primitives.primitives import *


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automatic feature engineering step")
    parser.add_argument(
        "--retailerid",
        type=int,
        help="MAIN_SYSTEM_ID",
    )
    parser.add_argument(
        "--maxdepth",
        type=int,
        default=2,
        help="Dictates the complexity of features built;"
        + " the higher the number, the more expensive the compute.",
    )
    args = parser.parse_args()
    selected_main_system_id = args.retailerid
    maxdepth = args.maxdepth
    print(f"\n\nStart DFS run for MAIN_SYSTEM_ID == {selected_main_system_id}")

    # Load datasets
    loans_df = (
        pd.read_excel("data/Loans_Data.xlsx")
        .drop(
            [
                # Low signal columns
                "INITIAL_COST",
                "INDEX",
                "REPAYMENT_ID",
                "FINAL_COST",
                "RETAILER_ID",
                # Columns populated after the fact, thus would lead to data leak
                "REPAYMENT_UPDATED",
                "SPENT",
                "TOTAL_FINAL_AMOUNT",
                "FIRST_TRIAL_BALANCE",
                "FIRST_TRAIL_DELAYS",
                "PAYMENT_AMOUNT",
                "LOAN_PAYMENT_DATE",
                "REPAYMENT_AMOUNT",
                "CUMMULATIVE_OUTSTANDING",
                "PAYMENT_STATUS",
            ],
            axis=1,
        )
        .query(f"MAIN_SYSTEM_ID == {selected_main_system_id}")
        .assign(
            MAIN_SYSTEM_ID=lambda x: x["MAIN_SYSTEM_ID"].astype("int64"),
            LOAN_ID=lambda x: x["LOAN_ID"].astype("int64"),
            LOAN_ISSUANCE_DATE=lambda x: x["LOAN_ISSUANCE_DATE"].astype("<M8[ns]"),
            LOAN_AMOUNT=lambda x: x["LOAN_AMOUNT"].astype("float64"),
            TOTAL_INITIAL_AMOUNT=lambda x: x["TOTAL_INITIAL_AMOUNT"].astype("float64"),
            INITIAL_DATE=lambda x: x["INITIAL_DATE"].astype("<M8[ns]"),
        )
    )

    fintech_df = (
        pd.read_csv(
            "data/Retailer_Transactions_Data.csv",
            header=0,
            dtype={
                "ID": np.dtype("int64"),
                "CREATED_AT": np.dtype("O"),
                "UPDATED_AT": np.dtype("O"),
                "AMOUNT": np.dtype("float64"),
                "FEES": np.dtype("float64"),
                "RETAILER_CUT": np.dtype("float64"),
                "STATUS": np.dtype("O"),
                "TOTAL_AMOUNT_INCLUDING_TAX": np.dtype("float64"),
                "TOTAL_AMOUNT_PAID": np.dtype("float64"),
                "WALLET_BALANCE_BEFORE_TRANSACTION": np.dtype("float64"),
                "MAIN_SYSTEM_ID": np.dtype("int64"),
            },
        )
        .query(f"MAIN_SYSTEM_ID == {selected_main_system_id}")
        .assign(
            CREATED_AT=lambda x: pd.to_datetime(
                x["CREATED_AT"], infer_datetime_format=True
            ),
            UPDATED_AT=lambda x: pd.to_datetime(
                x["UPDATED_AT"], infer_datetime_format=True
            ),
        )
    )

    ecommerce_df = (
        pd.read_csv(
            "data/Ecommerce_orders_Data.csv",
            header=0,
            dtype={
                "ORDER_ID": np.dtype("int64"),
                "MAIN_SYSTEM_ID": np.dtype("int64"),
                "ORDER_PRICE": np.dtype("float64"),
                "DISCOUNT": np.dtype("float64"),
                "ORDER_PRICE_AFTER_DISCOUNT": np.dtype("float64"),
                "ORDER_CREATION_DATE": np.dtype("O"),
            },
        )
        .query(f"MAIN_SYSTEM_ID == {selected_main_system_id}")
        .assign(
            ORDER_CREATION_DATE=lambda x: pd.to_datetime(
                x["ORDER_CREATION_DATE"], infer_datetime_format=True
            ),
        )
    )

    retailer_df = loans_df[["MAIN_SYSTEM_ID"]].drop_duplicates()

    # Create an entity set and add the retailers entity
    entity_set = ft.EntitySet(id="maxab_entity_set")
    relationships = []

    try:
        entity_set = entity_set.add_dataframe(
            dataframe_name="retailers",
            dataframe=retailer_df,
            index="MAIN_SYSTEM_ID",
        )
    except Exception as excpt:
        raise Exception(
            f"DFS failed for MAIN_SYSTEM_ID == {selected_main_system_id}"
        ) from excpt

    # Add the loans entity
    try:
        entity_set = entity_set.add_dataframe(
            dataframe_name="loans",
            dataframe=loans_df,
            index="LOAN_ID",
            time_index="LOAN_ISSUANCE_DATE",
        )
        rel_retailer_loans = ft.Relationship(
            entity_set,
            parent_dataframe_name="retailers",
            parent_column_name="MAIN_SYSTEM_ID",
            child_dataframe_name="loans",
            child_column_name="MAIN_SYSTEM_ID",
        )
        relationships.append(rel_retailer_loans)
    except Exception as excpt:
        raise Exception(
            f"DFS failed for MAIN_SYSTEM_ID == {selected_main_system_id}"
        ) from excpt
        pass

    # Add the sales entity and relationship
    try:
        entity_set = entity_set.add_dataframe(
            dataframe_name="sales",
            dataframe=fintech_df,
            index="ID",
            time_index="CREATED_AT",
            secondary_time_index={"UPDATED_AT": ["STATUS", "TOTAL_AMOUNT_PAID"]},
        )
        rel_retailer_sales = ft.Relationship(
            entity_set,
            parent_dataframe_name="retailers",
            parent_column_name="MAIN_SYSTEM_ID",
            child_dataframe_name="sales",
            child_column_name="MAIN_SYSTEM_ID",
        )
        relationships.append(rel_retailer_sales)
    except Exception:
        print(
            f"Fintech dataset seems to be empty for MAIN_SYSTEM_ID '{selected_main_system_id}'"
        )

    # Add the purchases entity
    try:
        entity_set = entity_set.add_dataframe(
            dataframe_name="purchases",
            dataframe=ecommerce_df,
            index="ORDER_ID",
            time_index="ORDER_CREATION_DATE",
        )
        rel_retailer_purchases = ft.Relationship(
            entity_set,
            parent_dataframe_name="retailers",
            parent_column_name="MAIN_SYSTEM_ID",
            child_dataframe_name="purchases",
            child_column_name="MAIN_SYSTEM_ID",
        )
        relationships.append(rel_retailer_purchases)
    except Exception:
        print(
            f"Ecommerce dataset seems to be empty for MAIN_SYSTEM_ID '{selected_main_system_id}'"
        )

    # Add the relationships to the entity set
    entity_set = entity_set.add_relationships(relationships)

    # Create list of Featuretools primitives
    ft_valid_primitives_tuple = ft.get_valid_primitives(
        entity_set, target_dataframe_name="loans", max_depth=2
    )
    FT_AGG_PRIMITIVES: List[str] = list(
        map(lambda x: x().name, ft_valid_primitives_tuple[0])
    )
    FT_TRANSFORM_PRIMITIVES: List[str] = list(
        map(lambda x: x().name, ft_valid_primitives_tuple[1])
    )

    # Remove buggy primitive 'expanding_count'
    try:
        FT_TRANSFORM_PRIMITIVES.remove("expanding_count")
    except:
        pass

    # Create list of TSFresh primitives
    TSFRESH_PARAMETERS = comprehensive_fc_parameters()

    # Creates a list where each element refers one combination of primitive(parameters)
    TSF_AGG_PRIMITIVES: List[AggregationPrimitive] = list(
        itertools.chain(
            *[
                primitives_from_fc_settings(
                    {
                        getattr(tsfresh_primitives, key).name: TSFRESH_PARAMETERS[
                            getattr(tsfresh_primitives, key).name
                        ]
                        or [{}]
                    }
                )
                for key in dir(tsfresh_primitives)
                if key[0].isupper()
                and key != "SUPPORTED_PRIMITIVES"
                and isinstance(
                    getattr(tsfresh_primitives, key)(
                        **(
                            TSFRESH_PARAMETERS[getattr(tsfresh_primitives, key).name]
                            or [{}]
                        )[0]
                    ),
                    AggregationPrimitive,
                )
            ]
        )
    )

    # Creates a list where each element refers to one combination of primitive(parameters)
    TSF_TRANSFORM_PRIMITIVES: List[TransformPrimitive] = list(
        itertools.chain(
            *[
                primitives_from_fc_settings(
                    {
                        getattr(tsfresh_primitives, key).name: TSFRESH_PARAMETERS[
                            getattr(tsfresh_primitives, key).name
                        ]
                        or [{}]
                    }
                )
                for key in dir(tsfresh_primitives)
                if key[0].isupper()
                and key != "SUPPORTED_PRIMITIVES"
                and isinstance(
                    getattr(tsfresh_primitives, key)(
                        **(
                            TSFRESH_PARAMETERS[getattr(tsfresh_primitives, key).name]
                            or [{}]
                        )[0]
                    ),
                    TransformPrimitive,
                )
            ]
        )
    )

    # Run deep feature synthesis to create features between the entities
    ft_featureframe, ft_feature_defs = ft.dfs(
        entityset=entity_set,
        target_dataframe_name="loans",
        verbose=0,
        agg_primitives=FT_AGG_PRIMITIVES,
        trans_primitives=FT_TRANSFORM_PRIMITIVES,
        max_depth=maxdepth,
        # max_features=1000,
        ignore_columns={
            "loans": ["LOAN_ID", "MAIN_SYSTEM_ID"],
            "sales": ["ID", "MAIN_SYSTEM_ID"],
            "purchases": ["ORDER_ID", "MAIN_SYSTEM_ID"],
        },
        n_jobs=1,
        cutoff_time_in_index=True,
    )

    tsf_featureframe, tsf_feature_defs = ft.dfs(
        entityset=entity_set,
        target_dataframe_name="loans",
        verbose=0,
        agg_primitives=TSF_AGG_PRIMITIVES,
        trans_primitives=TSF_TRANSFORM_PRIMITIVES,
        max_depth=maxdepth,
        # max_features=1000,
        ignore_columns={
            "loans": ["LOAN_ID", "MAIN_SYSTEM_ID"],
            "sales": ["ID", "MAIN_SYSTEM_ID"],
            "purchases": ["ORDER_ID", "MAIN_SYSTEM_ID"],
        },
        n_jobs=1,
        cutoff_time_in_index=True,
    )

    # Keep only LOAN_ID in the index
    ft_featureframe = ft_featureframe.reset_index(level=1)
    tsf_featureframe = tsf_featureframe.reset_index(level=1)

    # Get shared columns to avoid duplication in merge
    shared_columns = list(
        set(ft_featureframe.columns).intersection(set(tsf_featureframe.columns))
    )

    featureframe = pd.merge(
        ft_featureframe,
        tsf_featureframe.drop(shared_columns, axis=1),
        left_index=True,
        right_index=True,
    )
    feature_defs = list(set(ft_feature_defs).union(set(tsf_feature_defs)))

    # Clean up the feature frame a bit
    featureframe, feature_defs = ft.selection.remove_highly_null_features(
        featureframe, pct_null_threshold=0.25, features=feature_defs
    )
    featureframe, feature_defs = ft.selection.remove_low_information_features(
        featureframe, features=feature_defs
    )
    featureframe, feature_defs = ft.selection.remove_single_value_features(
        featureframe, features=feature_defs
    )
    featureframe, feature_defs = ft.selection.remove_single_value_features(
        featureframe, features=feature_defs
    )
    featureframe, feature_defs = ft.selection.remove_highly_correlated_features(
        featureframe, pct_corr_threshold=0.95, features=feature_defs
    )

    # Re-add column MAIN_SYSTEM_ID
    featureframe = (
        featureframe.join(
            loans_df[["LOAN_ID", "MAIN_SYSTEM_ID"]].drop_duplicates(),
            on="LOAN_ID",
            how="left"
        )
    )
    if not "LOAN_ID" in featureframe.columns:
        featureframe = featureframe.reset_index(drop=False)

    # Force all numeric columns to float and boolean to int for schema consistency
    # I will downcast them once Spark is able to merge schema of chunks
    featureframe = featureframe.astype(
        {
            **{
                key: "float"
                for key in featureframe.drop(
                    ["LOAN_ID", "MAIN_SYSTEM_ID"],
                    axis=1
                ).select_dtypes(include=["number"])
            },
            **{key: "int" for key in featureframe.select_dtypes(include=["boolean"])},
        }
    )

    # Write featureframe to storage
    dest_path = f"./data/featureframe-maxdepth{maxdepth}.parquet/MAIN_SYSTEM_ID={selected_main_system_id}/part.snappy.parquet"
    if not os.path.exists(dest_path):
        _ = subprocess.run(["mkdir", "-p", dest_path.rsplit("/", 1)[-1]])

    featureframe.to_parquet(dest_path, compression="snappy")

    _ = subprocess.run(
        f"mkdir -p data/featureframe.parquet/MAIN_SYSTEM_ID={selected_main_system_id}".split()
    )
    joblib.dump(feature_defs, f"data/featureframe-maxdepth{maxdepth}-definitions.joblib")
