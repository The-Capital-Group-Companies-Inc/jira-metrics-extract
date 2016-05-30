import argparse
import getpass
import json
import os.path
import datetime

import numpy as np
import pandas as pd

from jira import JIRA

from .config import config_to_options
from .cycletime import CycleTimeQueries
from . import charting

parser = argparse.ArgumentParser(description='Extract cycle time analytics data from JIRA.')
parser.add_argument('config', metavar='config.yml', help='Configuration file')
parser.add_argument('output', metavar='data.csv', nargs='?', help='Output file. Contains all issues described by the configuration file, metadata, and dates of entry to each state in the cycle.')
parser.add_argument('-v', dest='verbose', action='store_true', help='Verbose output')
parser.add_argument('-n', metavar='N', dest='max_results', type=int, help='Only fetch N most recently updated issues')
parser.add_argument('--format', metavar='[csv|json|xlsx]', help="Output format for data (default CSV)")
parser.add_argument('--cfd', metavar='cfd.csv', help='Calculate data to draw a Cumulative Flow Diagram and write to file. Hint: Plot as a (non-stacked) area chart.')
parser.add_argument('--scatterplot', metavar='scatterplot.csv', help='Calculate data to draw a cycle time scatter plot and write to file. Hint: Plot as a scatter chart.')
parser.add_argument('--histogram', metavar='histogram.csv', help='Calculate data to draw a cycle time histogram and write to file. Hint: Plot as a column chart.')
parser.add_argument('--throughput', metavar='throughput.csv', help='Calculate daily throughput data and write to file. Hint: Plot as a column chart.')
parser.add_argument('--percentiles', metavar='percentiles.csv', help='Calculate cycle time percentiles and write to file.')

parser.add_argument('--quantiles', metavar='0.3,0.5,0.75,0.85,0.95', help="Quantiles to use when calculating percentiles")
parser.add_argument('--backlog-column', metavar='<name>', help="Name of the backlog column. Defaults to the first column.")
parser.add_argument('--committed-column', metavar='<name>', help="Name of the column from which work is considered committed. Defaults to the second column.")
parser.add_argument('--final-column', metavar='<name>', help="Name of the final 'work' column. Defaults to the penultimate column.")
parser.add_argument('--done-column', metavar='<name>', help="Name of the 'done' column. Defaults to the last column.")
parser.add_argument('--throughput-window', metavar='60', type=int, default=60, help="How many days in the past to use for calculating throughput")

# TODO: Test charts
# TODO: README updates

if charting.HAVE_CHARTING:

    parser.add_argument('--charts-scatterplot', metavar='scatterplot.png', help="Draw cycle time scatter plot")
    parser.add_argument('--charts-histogram', metavar='histogram.png', help="Draw cycle time histogram")
    parser.add_argument('--charts-cfd', metavar='cfd.png', help="Draw Cumulative Flow Diagram")
    parser.add_argument('--charts-throughput', metavar='throughput.png', help="Draw weekly throughput chart with trend line")
    parser.add_argument('--charts-burnup', metavar='burnup.png', help="Draw simple burn-up chart")
    
    parser.add_argument('--charts-burnup-forecast', metavar='burnup-forecast.png', help="Draw burn-up chart with Monte Carlo simulation forecast to completion")
    parser.add_argument('--charts-burnup-forecast-target', metavar='<num stories>', type=int, help="Target completion scope for forecast. Defaults to current size of backlog.")
    parser.add_argument('--charts-burnup-forecast-trials', metavar='100', type=int, default=100, help="Number of iterations in Monte Carlo simulation.")
    
    parser.add_argument('--charts-wip', metavar='wip', help="Draw weekly WIP box plot")
    parser.add_argument('--charts-ageing-wip', metavar='ageing-wip.png', help="Draw current ageing WIP chart")
    parser.add_argument('--charts-net-flow', metavar='net-flow.png', help="Draw weekly net flow bar chart") 

def get_jira_client(connection):
    url = connection['domain']
    username = connection['username']
    password = connection['password']

    print "Connecting to", url

    if not username:
        username = raw_input("Username: ")

    if not password:
        password = getpass.getpass("Password: ")

    return JIRA({'server': url}, basic_auth=(username, password))

def to_json_string(value):
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, unicode):
        return value.encode('utf-8')
    if value in (None, np.NaN, pd.NaT):
        return ""

    try:
        return str(value)
    except TypeError:
        return value

def main():
    args = parser.parse_args()

    if not args.config:
        args.print_usage()
        return

    # Configuration 

    with open(args.config) as config:
        options = config_to_options(config.read())

    if args.max_results:
        options['settings']['max_results'] = args.max_results

    quantiles = [0.3, 0.5, 0.75, 0.85, 0.95]
    
    if args.quantiles:
        try:
            quantiles = [float(s.strip()) for s in args.quantiles.split(',')]
        except (AttributeError, ValueError,):
            print "Invalid value for --quantiles"
            args.print_usage()
            return

    output_format = args.format.lower() if args.format else 'csv'

    # Query JIRA

    jira = get_jira_client(options['connection'])

    q = CycleTimeQueries(jira, **options['settings'])

    print "Fetching issues (this could take some time)"
    cycle_data = q.cycle_data(verbose=args.verbose)
    
    cfd_data = q.cfd(cycle_data)
    scatter_data = q.scatterplot(cycle_data)
    histogram_data = q.histogram(cycle_data)
    percentile_data = q.percentiles(cycle_data, percentiles=quantiles)
    
    daily_throughput_data = q.throughput_data(
        cycle_data[cycle_data['completed_timestamp'] >= (datetime.date.today() - datetime.timedelta(days=args.throughput_window))],
    )
    
    backlog_column = args.backlog_column or cfd_data.columns[0]
    committed_column = args.committed_column or cfd_data.columns[1]
    final_column = args.final_column or cfd_data.columns[-2]
    done_column = args.done_column or cfd_data.columns[-1]

    cycle_names = [s['name'] for s in q.settings['cycle']]
    field_names = sorted(options['settings']['fields'].keys())
    query_attribute_names = [q.settings['query_attribute']] if q.settings['query_attribute'] else []
    
    # Write files
    
    if args.output:
        print "Writing cycle data to", args.output
        
        header = ['ID', 'Link', 'Name'] + cycle_names + ['Type', 'Status', 'Resolution'] + field_names + query_attribute_names
        columns = ['key', 'url', 'summary'] + cycle_names + ['issue_type', 'status', 'resolution'] + field_names + query_attribute_names
        
        if output_format == 'json':
            values = [header] + [map(to_json_string, row) for row in cycle_data[columns].values.tolist()]
            with open(args.output, 'w') as out:
                out.write(json.dumps(values))
        elif output_format == 'xlsx':
            cycle_data.to_excel(args.output, 'Cycle data', columns=columns, header=header, index=False)
        else:
            cycle_data.to_csv(args.output, columns=columns, header=header, date_format='%Y-%m-%d', index=False)

    if args.cfd:
        print "Writing Cumulative Flow Diagram data to", args.cfd
        if output_format == 'json':
            cfd_data.to_json(args.cfd, date_format='iso')
        elif output_format == 'xlsx':
            cfd_data.to_excel(args.cfd, 'CFD')
        else:   
            cfd_data.to_csv(args.cfd)

    if args.scatterplot:
        print "Writing cycle time scatter plot data to", args.scatterplot
        if output_format == 'json':
            scatter_data.to_json(args.scatterplot, date_format='iso')
        elif output_format == 'xlsx':
            scatter_data.to_excel(args.scatterplot, 'Scatter', index=False)
        else:
            scatter_data.to_csv(args.scatterplot, index=False)

    if args.percentiles:
        print "Writing cycle time percentiles", args.percentiles
        if output_format == 'json':
            percentile_data.to_json(args.percentiles, date_format='iso')
        elif output_format == 'xlsx':
            percentile_data.to_frame(name='percentiles').to_excel(args.percentiles, 'Percentiles', header=True)
        else:
            percentile_data.to_csv(args.percentiles, header=True)

    if args.histogram:
        print "Writing cycle time histogram data to", args.histogram
        if output_format == 'json':
            histogram_data.to_json(args.histogram, date_format='iso')
        elif output_format == 'xlsx':
            histogram_data.to_frame(name='histogram').to_excel(args.histogram, 'Histogram', header=True)
        else:
            histogram_data.to_csv(args.histogram, header=True)
    
    if args.throughput:
        print "Writing throughput data to", args.throughput
        if output_format == 'json':
            daily_throughput_data.to_json(args.throughput, date_format='iso')
        elif output_format == 'xlsx':
            daily_throughput_data.to_excel(args.throughput, 'Throughput', header=True)
        else:
            daily_throughput_data.to_csv(args.throughput, header=True)

    # Output charts (if we have the right things installed)
    if charting.HAVE_CHARTING:
    
        charting.set_context()
    
        if args.charts_scatterplot:
            print "Drawing scatterplot in", args.charts_scatterplot
            charting.set_style('darkgrid')
            ax = charting.cycle_time_scatterplot(cycle_data, percentiles=quantiles)
            fig = ax.get_figure()
            fig.savefig(args.charts_scatterplot, bbox_inches='tight', dpi=300) 

        if args.charts_histogram:
            print "Drawing histogram in", args.charts_histogram
            charting.set_style('darkgrid')
            ax = charting.cycle_time_histogram(cycle_data, percentiles=quantiles)
            fig = ax.get_figure()
            fig.savefig(args.charts_histogram, bbox_inches='tight', dpi=300) 
        
        if args.charts_cfd:
            print "Drawing CFD in", args.charts_cfd
            charting.set_style('whitegrid')
            ax = charting.cfd(cfd_data)
            fig = ax.get_figure()
            fig.savefig(args.charts_cfd, bbox_inches='tight', dpi=300) 
        
        if args.charts_throughput:
            print "Drawing throughput chart in", args.charts_throughput
            charting.set_style('darkgrid')
            ax = charting.throughput_trend_chart(daily_throughput_data)
            fig = ax.get_figure()
            fig.savefig(args.charts_throughput, bbox_inches='tight', dpi=300) 
        
        if args.charts_burnup:
            print "Drawing burnup chart in", args.charts_burnup
            charting.set_style('whitegrid')
            ax = charting.burnup(cfd_data, backlog_column=backlog_column, done_column=done_column)
            fig = ax.get_figure()
            fig.savefig(args.charts_burnup, bbox_inches='tight', dpi=300) 
        
        if args.charts_burnup_forecast:
            target = args.charts_burnup_forecast_target or None
            trials = args.charts_burnup_forecast_trials or 100

            print "Drawing burnup foreacst chart in", args.charts_burnup_forecast
            charting.set_style('whitegrid')
            ax = charting.burnup_forecast(cfd_data, daily_throughput_data,
                    trials=trials, target=target,
                    backlog_column=backlog_column, done_column=done_column,
                    percentiles=quantiles
                )
            fig = ax.get_figure()
            fig.savefig(args.charts_burnup_forecast, bbox_inches='tight', dpi=300) 

        if args.charts_wip:
            print "Drawing WIP chart in", args.charts_wip
            charting.set_style('darkgrid')
            ax = charting.wip_chart(cfd_data, start_column=committed_column, end_column=final_column)
            fig = ax.get_figure()
            fig.savefig(args.charts_wip, bbox_inches='tight', dpi=300) 
        
        if args.charts_ageing_wip:
            print "Drawing ageing WIP chart in", args.charts_ageing_wip
            charting.set_style('whitegrid')
            ax = charting.ageing_wip_chart(cycle_data, start_column=committed_column, end_column=final_column, done_column=done_column)
            fig = ax.get_figure()
            fig.savefig(args.charts_ageing_wip, bbox_inches='tight', dpi=300) 
        
        if args.charts_net_flow:
            print "Drawing net flow chart in", args.charts_net_flow
            charting.set_style('darkgrid')
            ax = charting.net_flow_chart(cfd_data, start_column=committed_column, end_column=done_column)
            fig = ax.get_figure()
            fig.savefig(args.charts_net_flow, bbox_inches='tight', dpi=300) 

    print "Done"
