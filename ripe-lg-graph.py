#!/usr/bin/python3
# -*- coding: utf-8 -*-

from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import argparse
import dns.resolver
import ipaddress
import os
import pydot
import random
import requests

looking_glass_url = "https://stat.ripe.net/data/looking-glass/data.json"

# please check https://stat.ripe.net/docs/data_api#RulesOfUsage
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


def is_valid(a:str):
    try:
        ipaddress.ip_address(a)
        return True
    except ValueError:
        try:
            ipaddress.ip_network(a, strict=True)
            return True
        except ValueError:
            return False


def form_params(a:str=""):
    params = {
        "sourceapp": sourceapp_name,
        "soft_limit": "ignore"
    }

    if (a != ""):
        params["resource"] = a

    return params


def get_rrc_data(a:str):
    parted_url = list(urlparse(looking_glass_url))
    
    query = dict(parse_qsl(parted_url[4]))
    query.update(form_params(a))

    parted_url[4] = urlencode(query)
    final_url = urlunparse(parted_url)

    r = requests.get(final_url)
    r.raise_for_status()
    data = r.json()

    if (data["messages"]):
        for message_array in data["messages"]:
            print(f"RIPE {message_array[0]}: {message_array[1]}")

    if (len(data["data"]["rrcs"]) == 0):
        raise AddressOrPrefixNotFoundError("Prefix or address is not found on RIPE NCC's RIS.")

    global target
    target = data["data"]["parameters"]["resource"]

    returning_data = {}

    for rrc_dict in data["data"]["rrcs"]:
        rrc_name = rrc_dict["rrc"]

        if (rrc_name not in returning_data):
            returning_data[rrc_name] = []

        for rrc_peer in rrc_dict["peers"]:
            as_path = rrc_peer["as_path"]
            as_path_list = []

            # this is done to strip prepends
            for as_number in as_path.split(" "):
                if ("".join(as_path_list[-1:]) == as_number):
                    continue
                as_path_list.append(as_number)

            returning_data[rrc_name].append(" ".join(as_path_list))

    return returning_data


def query_asn_info(n:str):
    try:
        data = dns.resolver.query(f"AS{n}.asn.cymru.com", "TXT").response.answer[0][-1].to_text().replace("'","").replace('"','')
    except:
        return " "*5
    return [ field.strip() for field in data.split("|") ]


def get_as_name(_as):
    if not _as:
        return "AS?????"

    if not _as.isdigit():
        return _as.strip()

    name = query_asn_info(_as)[-1].replace(" ","\r",1)
    return f"AS{_as} | {name}"


def make_bgpmap(rrc:str, paths:list):
    graph = pydot.Dot('BGPMAP', graph_type='digraph')

    nodes = {}
    edges = {}

    def escape(label):
        label = label.replace("&", "&amp;")
        label = label.replace(">", "&gt;")
        label = label.replace("<", "&lt;")
        return label

    def add_node(_as, **kwargs):
        if _as not in nodes:
            kwargs["label"] = '<<TABLE CELLBORDER="0" BORDER="0" CELLPADDING="0" CELLSPACING="0"><TR><TD ALIGN="CENTER">' + escape(kwargs.get("label", get_as_name(_as))).replace("\r","<BR/>") + "</TD></TR></TABLE>>"
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

    add_node(rrc, label= f"{rrc.upper()}", shape="box", fillcolor="#F5A9A9")

    previous_as = None
    first = True
    for asmap in paths:
        previous_as = rrc
        color = "#%x" % random.randint(0, 16777215)

        hop = False
        hop_label = ""
        for _as in asmap.split(" "):
            if not hop:
                hop = True
                hop_label = _as
                if first:
                    hop_label = hop_label + "*"

            if _as == asmap[-1]:
                add_node(_as, fillcolor="#F5A9A9", shape="box")
            else:
                add_node(_as, fillcolor=(first and "#F5A9A9" or "white"))
            if hop_label:
                edge = add_edge(nodes[previous_as], nodes[_as], label=hop_label, fontsize="7")
            else:
                edge = add_edge(nodes[previous_as], nodes[_as], fontsize="7")

            hop_label = ""

            if first or _as == asmap[-1]:
                edge.set_style("bold")
                edge.set_color("red")
            elif edge.get_style() != "bold":
                edge.set_style("dashed")
                edge.set_color(color)

            previous_as = _as
        first = False

    add_node(target, fillcolor="#F5A9A9", shape="box")
    final_edge = add_edge(nodes[_as], nodes[target], fontsize="7")
    final_edge.set_style("bold")
    final_edge.set_color("red")

    graph.write(f"./output/{target_folder}/png/{rrc}.png", format="png")
    graph.write(f"./output/{target_folder}/svg/{rrc}.svg", format="svg")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a routing graph with data from RIPE NCC's RIS.")

    parser.add_argument(
        "address_prefix", help="IP prefix or address, will not search for the nearest ", type=str
    )

    args = parser.parse_args()

    if (is_valid(args.address_prefix)):
        rrc_path_data = get_rrc_data(args.address_prefix)
        os.makedirs(f"./output/{target_folder}/png")
        os.makedirs(f"./output/{target_folder}/svg")
        for rrc, paths in rrc_path_data.items():
            make_bgpmap(rrc, paths)
        print("Done!")
    else:
        raise AddressOrPrefixNotFoundError("Entered address or prefix is invalid.")
