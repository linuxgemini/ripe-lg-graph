#!/usr/bin/python3
# -*- coding: utf-8 -*-

import argparse
import dns.resolver
import pydot
import ipaddress
import os
import random
import requests
import shutil
import sys
import typing

from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from types import TracebackType

looking_glass_url = "https://stat.ripe.net/data/looking-glass/data.json"

# See: https://stat.ripe.net/docs/data_api#RulesOfUsage
sourceapp_name = "ripe-lg-graph_py"

# DON'T TOUCH IF YOU DON'T KNOW WHAT YOU'RE DOING
target_folder = datetime.now().strftime("%Y-%m-%d %H%M%S")
target = ""


class AddressOrPrefixNotFoundError(Exception):
    """Exception raised for errors in the input.

    Attributes:
        message -- explanation of the error
    """

    def __init__(self, message):
        super().__init__(message)


def is_valid(address_prefix:str) -> bool:
    try:
        ipaddress.ip_address(address_prefix)
        return True
    except ValueError:
        try:
            ipaddress.ip_network(address_prefix, strict=True)
            return True
        except ValueError:
            return False


def process_rrc_options(raw_rrc_list_str:str) -> typing.Union[str, typing.List[str]]:
    returning_list = []

    if raw_rrc_list_str.isdigit():
        rrc_id = int(raw_rrc_list_str)
        returning_list.append(f"{rrc_id:02}")
    elif "," in raw_rrc_list_str:
        raw_list = raw_rrc_list_str.split(",")
        for rrc_id_str in raw_list:
            if not rrc_id_str.isdigit():
                continue
            rrc_id = int(rrc_id_str)
            returning_list.append(f"{rrc_id:02}")

    if len(returning_list) != 0:
        return returning_list
    else:
        return ""


def form_params(resource_param:str="") -> typing.Dict[str, str]:
    params = {
        "sourceapp": sourceapp_name,
        "soft_limit": "ignore"
    }

    if (resource_param != ""):
        params["resource"] = resource_param

    return params


def get_rrc_data(address_prefix:str, rrc_list:typing.Union[str, typing.List[str]]="") -> typing.Dict[str, typing.Dict[str, typing.Union[str, typing.List[str]]]]:
    parted_url = list(urlparse(looking_glass_url))

    query = dict(parse_qsl(parted_url[4]))
    query.update(form_params(address_prefix))

    parted_url[4] = urlencode(query)
    final_url = urlunparse(parted_url)

    print("Contacting RIPE NCC RIS looking glass...")
    r = requests.get(final_url)
    r.raise_for_status()
    data = r.json()

    if (data["messages"]):
        for message_array in data["messages"]:
            if (message_array[0].lower() == "error"):
                raise Exception(message_array[1])
            else:
                print(f"RIPE {message_array[0]}: {message_array[1]}")

    if (len(data["data"]["rrcs"]) == 0):
        raise AddressOrPrefixNotFoundError("Prefix or address is not found on RIPE NCC's RIS.")

    global target
    target = data["data"]["parameters"]["resource"]

    raw_returning_data = {}

    for rrc_dict in data["data"]["rrcs"]:
        rrc_name = rrc_dict['rrc']

        if (rrc_name not in raw_returning_data):
            raw_returning_data[rrc_name] = {
                "location": rrc_dict["location"],
                "paths": []
            }

        for rrc_peer in rrc_dict["peers"]:
            as_path = rrc_peer["as_path"]
            as_path_list = []

            # this is done to strip prepends
            for as_number in as_path.split(" "):
                if ("".join(as_path_list[-1:]) == as_number):
                    continue
                as_path_list.append(as_number)

            raw_returning_data[rrc_name]["paths"].append(" ".join(as_path_list))

    if rrc_list == "":
        print("Processing all available RRCs...")
        returning_data = dict(sorted(raw_returning_data.items()))
    else:
        pre_proc_returning_data = {}
        for rrc in rrc_list:
            rrc_name = f"RRC{rrc}"
            if rrc_name not in raw_returning_data:
                print(f"{rrc_name} is either invalid or not found, skipping...")
                continue
            pre_proc_returning_data[rrc_name] = raw_returning_data[rrc_name]
        if len(pre_proc_returning_data) == 0:
            print("None of the specified RRCs are in the result list, passing all RRCs...")
            pre_proc_returning_data = raw_returning_data
        returning_data = dict(sorted(pre_proc_returning_data.items()))

    return returning_data


def query_asn_info(asn:str) -> str:
    try:
        data = dns.resolver.resolve(
            f"AS{asn}.asn.cymru.com", "TXT"
        ).response.answer[0][-1].to_text().replace("'","").replace('"','')
    except:
        return " "*5
    return [ field.strip() for field in data.split("|") ]


def get_as_name(_as:str) -> str:
    if not _as:
        return "AS?????"

    if not _as.isdigit():
        return _as.strip()

    name = query_asn_info(_as)[-1].replace(" ","\r",1)
    return f"AS{_as} | {name}"


def make_bgpmap(rrc:str, rrc_data_dict:typing.Dict[str, typing.Union[str, typing.List[str]]]) -> True:
    rrc_full = f"{rrc} - {rrc_data_dict['location']}"
    print(f"Now processing: {rrc_full}")

    graph = pydot.Dot('BGPMAP', graph_type='digraph')

    nodes = {}
    edges = {}

    def escape(label):
        label = label.replace("&", "&amp;")
        label = label.replace(">", "&gt;")
        label = label.replace("<", "&lt;")
        return label

    def add_node(_as, **kwargs):
        carriage_return = "\r"

        if _as not in nodes:
           kwargs["label"] = f"<<TABLE CELLBORDER=\"0\" BORDER=\"0\" CELLPADDING=\"0\" CELLSPACING=\"0\"><TR><TD ALIGN=\"CENTER\">{escape(kwargs.get('label', get_as_name(_as))).replace(carriage_return,'<BR/>')}</TD></TR></TABLE>>"
            nodes[_as] = pydot.Node(_as, style="filled", fontsize="10", **kwargs)
            graph.add_node(nodes[_as])

        return nodes[_as]

    def add_edge(_previous_as, _as, **kwargs):
        kwargs["splines"] = "true"
        force = kwargs.get("force", False)

        edge_tuple = (_previous_as, _as)

        if force or edge_tuple not in edges:
            edge = pydot.Edge(*edge_tuple, **kwargs)
            graph.add_edge(edge)
            edges[edge_tuple] = edge
        elif "label" in kwargs and kwargs["label"]:
            e = edges[edge_tuple]
            label_without_star = kwargs["label"].replace("*", "")

            if e.get_label() is not None:
                labels = e.get_label().split("\r")
            else:
                return edges[edge_tuple]

            if "%s*" % label_without_star not in labels:
                labels = [ kwargs["label"] ]  + [ l for l in labels if not l.startswith(label_without_star) ]
                labels = sorted(labels, key=lambda x: x.endswith("*") and -1 or 1)

                label = escape("\r".join(labels))
                e.set_label(label)

        return edges[edge_tuple]

    add_node(rrc_full, label=rrc, shape="box", fillcolor="#F5A9A9")

    previous_as = None
    first = True

    for asmap in rrc_data_dict["paths"]:
        previous_as = rrc_full
        color = "#%x" % random.randint(0, 16777215)

        hop = False
        hop_label = ""

        for _as in asmap.split(" "):

            if not hop:
                hop = True
                hop_label = _as
                if first:
                    hop_label = hop_label + "*"

            if (_as == asmap[-1]):
                add_node(_as, fillcolor="#F5A9A9", shape="box")
            else:
                add_node(_as, fillcolor=(first and "#F5A9A9" or "white"))

            if hop_label:
                edge = add_edge(nodes[previous_as], nodes[_as], label=hop_label, fontsize="7")
            else:
                edge = add_edge(nodes[previous_as], nodes[_as], fontsize="7")

            hop_label = ""

            if (first or _as == asmap[-1]):
                edge.set_style("bold")
                edge.set_color("red")
            elif edge.get_style() != "bold":
                edge.set_style("dashed")
                edge.set_color(color)

            previous_as = _as

        first = False

    add_node("Prefix", label=target, fillcolor="#F5A9A9", shape="box")
    final_edge = add_edge(nodes[_as], nodes["Prefix"], fontsize="7")
    final_edge.set_style("bold")
    final_edge.set_color("red")

    graph.write(f"./output/{target_folder}/png/{rrc}.png", format="png")
    graph.write(f"./output/{target_folder}/svg/{rrc}.svg", format="svg")

    return True


def except_clearence_hook(exctype:typing.Type[BaseException], value:BaseException, traceback:TracebackType) -> None:
    if (os.path.exists(f"./output/{target_folder}")):
        shutil.rmtree(f"./output/{target_folder}")
    sys.__excepthook__(exctype, value, traceback)

sys.excepthook = except_clearence_hook


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a routing graph with data from RIPE NCC's RIS.")

    parser.add_argument(
        "--rrc", help="ID(s) of the RRC for graphing, process all if none specified (comma seperated if multiple)", type=str, required=False, default=""
    )

    parser.add_argument(
        "address_prefix", help="IP prefix or address, will not search for the nearest announced object.", type=str
    )

    args = parser.parse_args()

    if (is_valid(args.address_prefix)):
        rrc_path_data = get_rrc_data(args.address_prefix, process_rrc_options(args.rrc))

        os.makedirs(f"./output/{target_folder}/png")
        os.makedirs(f"./output/{target_folder}/svg")

        for rrc, rrc_data_dict in rrc_path_data.items():
            make_bgpmap(rrc, rrc_data_dict)

        print("\nDone!")
    else:
        raise AddressOrPrefixNotFoundError("Entered address or prefix is invalid.")

