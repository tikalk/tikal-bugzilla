#!/usr/bin/perl -wT
# -*- Mode: perl; indent-tabs-mode: nil -*-
#
# The contents of this file are subject to the Mozilla Public
# License Version 1.1 (the "License"); you may not use this file
# except in compliance with the License. You may obtain a copy of
# the License at http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS
# IS" basis, WITHOUT WARRANTY OF ANY KIND, either express or
# implied. See the License for the specific language governing
# rights and limitations under the License.
#
# The Original Code is the Bugzilla Bug Tracking System.
#
# The Initial Developer of the Original Code is Netscape Communications
# Corporation. Portions created by Netscape are
# Copyright (C) 1998 Netscape Communications Corporation. All
# Rights Reserved.
#
# Contributor(s): Gervase Markham <gerv@gerv.net>
#                 <rdean@cambianetworks.com>

use strict;
use lib qw(. lib);

use Bugzilla;
use Bugzilla::Constants;
use Bugzilla::Util;
use Bugzilla::Error;
use Bugzilla::Field;
use Bugzilla::Menu;

my $cgi = Bugzilla->cgi;
my $template = Bugzilla->template;
my $buffer = $cgi->query_string();

# Go straight back to query.cgi if we are adding a boolean chart.
if (grep(/^cmd-/, $cgi->param())) {
    my $params = $cgi->canonicalise_query("format", "ctype");
    my $location = "query.cgi?format=" . $cgi->param('query_format') . 
      ($params ? "&$params" : "");

    print $cgi->redirect($location);
    exit;
}

use Bugzilla::Search;

Bugzilla->login();

my $vars = Bugzilla::Menu::PopulateClassificationAndProducts();

my $dbh = Bugzilla->switch_to_shadow_db();

my $action = $cgi->param('action') || 'menu';
# If we're editing a saved report, use the existing report name as default for
# the "Remember report as" field.
$vars->{'report_based_on'} = $cgi->param('report_based_on');

if ($action eq "menu") {
    # No need to do any searching in this case, so bail out early.
    print $cgi->header();
    $template->process("reports/menu.html.tmpl", $vars)
      || ThrowTemplateError($template->error());
    exit;
}

# save report or update existing one
my $userid = Bugzilla->user->id;
my $cmdtype = $cgi->param('cmdtype') || '';
if ($cmdtype eq "savereport") {
	Bugzilla->login(LOGIN_REQUIRED);
 	my $report_name = $cgi->param('newreportname');
 	my $tofooter = 1;
 	my $existed_before = InsertNamedReport($report_name,
 	  	                                   scalar $cgi->param('newreport'),
 	  	                                   $tofooter);
 	if ($existed_before) {
 		$vars->{'message'} = "updated_named_report";
 	}
 	else {
 		$vars->{'message'} = "new_named_report";
 	}
 	  	 
 	# Make sure to invalidate any cached report data, so that the footer is
 	# correctly displayed
 	Bugzilla->user->flush_reports_cache();
	$vars->{'reportname'} = $report_name;
 	  	 
 	print $cgi->header();
 	$template->process("global/message.html.tmpl", $vars)
 	  	           || ThrowTemplateError($template->error());
 	exit;
 	  	 
} # forget report
elsif ($cmdtype eq "forget") {
	Bugzilla->login(LOGIN_REQUIRED);
 	# Copy the name into a variable, so that we can trick_taint it for
 	# the DB. We know it's safe, because we're using placeholders in
 	# the SQL, and the SQL is only a DELETE.
 	my $reportname = $cgi->param('reportname');
 	trick_taint($reportname);
 	my ($query_id) = $dbh->selectrow_array('SELECT id FROM namedreports
                                                    WHERE userid = ?
                                                      AND name   = ?',
                                                  undef, ($userid, $reportname));
        if (!$query_id) {
            # The user has no query of this name. Play along.
        }
        else {
            $dbh->do('DELETE FROM namedreports
                            WHERE id = ?',
                     undef, $query_id);
            $dbh->do('DELETE FROM namedreports_link_in_footer
                            WHERE namedreport_id = ?',
                     undef, $query_id);
            $dbh->do('DELETE FROM namedreport_group_map
                            WHERE namedreport_id = ?',
                     undef, $query_id);
        }
 	  	 
 	# Now reset the cached reports
 	Bugzilla->user->flush_reports_cache();
 	  	 
 	print $cgi->header();
 	# Generate and return the UI (HTML page) from the appropriate template.
 	$vars->{'message'} = "named_report_gone";
 	$vars->{'namedcmd'} = $cgi->param('reportname');
 	$vars->{'url'} = "report.cgi";
 	$template->process("global/message.html.tmpl", $vars)
 	  	           || ThrowTemplateError($template->error());
 	exit;
 	  	 
}

my $col_field = $cgi->param('x_axis_field') || '';
my $row_field = $cgi->param('y_axis_field') || '';
my $tbl_field = $cgi->param('z_axis_field') || '';

if (!($col_field || $row_field || $tbl_field)) {
    ThrowUserError("no_axes_defined");
}

my $width = $cgi->param('width');
my $height = $cgi->param('height');

if (defined($width)) {
   (detaint_natural($width) && $width > 0)
     || ThrowCodeError("invalid_dimensions");
   $width <= 2000 || ThrowUserError("chart_too_large");
}

if (defined($height)) {
   (detaint_natural($height) && $height > 0)
     || ThrowCodeError("invalid_dimensions");
   $height <= 2000 || ThrowUserError("chart_too_large");
}

# These shenanigans are necessary to make sure that both vertical and 
# horizontal 1D tables convert to the correct dimension when you ask to
# display them as some sort of chart.
if (defined $cgi->param('format') && $cgi->param('format') eq "table") {
    if ($col_field && !$row_field) {    
        # 1D *tables* should be displayed vertically (with a row_field only)
        $row_field = $col_field;
        $col_field = '';
    }
}
else {
    if ($row_field && !$col_field) {
        # 1D *charts* should be displayed horizontally (with an col_field only)
        $col_field = $row_field;
        $row_field = '';
    }
}

# Valid bug fields that can be reported on.
my @columns = qw(
    assigned_to
    reporter
    qa_contact
    component
    classification
    version
    votes
    keywords
    target_milestone
    entity
    resolver
);
# Single-select fields (custom or not) are also accepted as valid.
my @single_selects = Bugzilla->get_fields({ type => FIELD_TYPE_SINGLE_SELECT,
                                            obsolete => 0 });
push(@columns, map { $_->name } @single_selects);
my %valid_columns = map { $_ => 1 } @columns;

# Validate the values in the axis fields or throw an error.
!$row_field 
  || ($valid_columns{$row_field} && trick_taint($row_field))
  || ThrowCodeError("report_axis_invalid", {fld => "x", val => $row_field});
!$col_field 
  || ($valid_columns{$col_field} && trick_taint($col_field))
  || ThrowCodeError("report_axis_invalid", {fld => "y", val => $col_field});
!$tbl_field 
  || ($valid_columns{$tbl_field} && trick_taint($tbl_field))
  || ThrowCodeError("report_axis_invalid", {fld => "z", val => $tbl_field});

my @axis_fields = ($row_field || EMPTY_COLUMN, 
                   $col_field || EMPTY_COLUMN,
                   $tbl_field || EMPTY_COLUMN);

# Clone the params, so that Bugzilla::Search can modify them
my $params = new Bugzilla::CGI($cgi);
my $search = new Bugzilla::Search('fields' => \@axis_fields, 
                                  'params' => $params);
my $query = $search->getSQL();

$::SIG{TERM} = 'DEFAULT';
$::SIG{PIPE} = 'DEFAULT';

my $results = $dbh->selectall_arrayref($query);

# We have a hash of hashes for the data itself, and a hash to hold the 
# row/col/table names.
my %data;
my %names;

# Read the bug data and count the bugs for each possible value of row, column
# and table.
#
# We detect a numerical field, and sort appropriately, if all the values are
# numeric.
my $col_isnumeric = 1;
my $row_isnumeric = 1;
my $tbl_isnumeric = 1;

foreach my $result (@$results) {
    my ($row, $col, $tbl) = @$result;

    # handle empty dimension member names
    $row = ' ' if ($row eq '');
    $col = ' ' if ($col eq '');
    $tbl = ' ' if ($tbl eq '');

    $row = "" if ($row eq EMPTY_COLUMN);
    $col = "" if ($col eq EMPTY_COLUMN);
    $tbl = "" if ($tbl eq EMPTY_COLUMN);
    
    # account for the fact that names may start with '_' or '.'.  Change this 
    # so the template doesn't hide hash elements with those keys
    $row =~ s/^([._])/ $1/;
    $col =~ s/^([._])/ $1/;
    $tbl =~ s/^([._])/ $1/;

    $data{$tbl}{$col}{$row}++;
    $names{"col"}{$col}++;
    $names{"row"}{$row}++;
    $names{"tbl"}{$tbl}++;
    
    $col_isnumeric &&= ($col =~ /^-?\d+(\.\d+)?$/o);
    $row_isnumeric &&= ($row =~ /^-?\d+(\.\d+)?$/o);
    $tbl_isnumeric &&= ($tbl =~ /^-?\d+(\.\d+)?$/o);
}

my @col_names = @{get_names($names{"col"}, $col_isnumeric, $col_field)};
my @row_names = @{get_names($names{"row"}, $row_isnumeric, $row_field)};
my @tbl_names = @{get_names($names{"tbl"}, $tbl_isnumeric, $tbl_field)};

# The GD::Graph package requires a particular format of data, so once we've
# gathered everything into the hashes and made sure we know the size of the
# data, we reformat it into an array of arrays of arrays of data.
push(@tbl_names, "-total-") if (scalar(@tbl_names) > 1);
    
my @image_data;
foreach my $tbl (@tbl_names) {
    my @tbl_data;
    push(@tbl_data, \@col_names);
    foreach my $row (@row_names) {
        my @col_data;
        foreach my $col (@col_names) {
            $data{$tbl}{$col}{$row} = $data{$tbl}{$col}{$row} || 0;
            push(@col_data, $data{$tbl}{$col}{$row});
            if ($tbl ne "-total-") {
                # This is a bit sneaky. We spend every loop except the last
                # building up the -total- data, and then last time round,
                # we process it as another tbl, and push() the total values 
                # into the image_data array.
                $data{"-total-"}{$col}{$row} += $data{$tbl}{$col}{$row};
            }
        }

        push(@tbl_data, \@col_data);
    }
    
    unshift(@image_data, \@tbl_data);
}

$vars->{'col_field'} = $col_field;
$vars->{'row_field'} = $row_field;
$vars->{'tbl_field'} = $tbl_field;
$vars->{'time'} = localtime(time());

$vars->{'col_names'} = \@col_names;
$vars->{'row_names'} = \@row_names;
$vars->{'tbl_names'} = \@tbl_names;

$vars->{'reportname'} = $cgi->param('reportname') || '';
$vars->{'reporttype'} = $cgi->param('reporttype') || '';
$vars->{'urlreportpart'} = $buffer;

# Below a certain width, we don't see any bars, so there needs to be a minimum.
if ($width && $cgi->param('format') eq "bar") {
    my $min_width = (scalar(@col_names) || 1) * 20;

    if (!$cgi->param('cumulate')) {
        $min_width *= (scalar(@row_names) || 1);
    }

    $vars->{'min_width'} = $min_width;
}

$vars->{'width'} = $width if $width;
$vars->{'height'} = $height if $height;

$vars->{'query'} = $query;
$vars->{'debug'} = $cgi->param('debug');

my $formatparam = $cgi->param('format');

if ($action eq "wrap") {
    # So which template are we using? If action is "wrap", we will be using
    # no format (it gets passed through to be the format of the actual data),
    # and either report.csv.tmpl (CSV), or report.html.tmpl (everything else).
    # report.html.tmpl produces an HTML framework for either tables of HTML
    # data, or images generated by calling report.cgi again with action as
    # "plot".
    $formatparam =~ s/[^a-zA-Z\-]//g;
    trick_taint($formatparam);
    $vars->{'format'} = $formatparam;
    $formatparam = '';

    # We need to keep track of the defined restrictions on each of the 
    # axes, because buglistbase, below, throws them away. Without this, we
    # get buglistlinks wrong if there is a restriction on an axis field.
    $vars->{'col_vals'} = join("&", $buffer =~ /[&?]($col_field=[^&]+)/g);
    $vars->{'row_vals'} = join("&", $buffer =~ /[&?]($row_field=[^&]+)/g);
    $vars->{'tbl_vals'} = join("&", $buffer =~ /[&?]($tbl_field=[^&]+)/g);
    
    # We need a number of different variants of the base URL for different
    # URLs in the HTML.
    $vars->{'buglistbase'} = $cgi->canonicalise_query(
                                 "x_axis_field", "y_axis_field", "z_axis_field",
                               "ctype", "format", "query_format", @axis_fields);
    $vars->{'imagebase'}   = $cgi->canonicalise_query( 
                    $tbl_field, "action", "ctype", "format", "width", "height");
    $vars->{'switchbase'}  = $cgi->canonicalise_query( 
                "query_format", "action", "ctype", "format", "width", "height");
    $vars->{'data'} = \%data;
}
elsif ($action eq "plot") {
    # If action is "plot", we will be using a format as normal (pie, bar etc.)
    # and a ctype as normal (currently only png.)
    $vars->{'cumulate'} = $cgi->param('cumulate') ? 1 : 0;
    $vars->{'x_labels_vertical'} = $cgi->param('x_labels_vertical') ? 1 : 0;
    $vars->{'data'} = \@image_data;
}
else {
    ThrowCodeError("unknown_action", {action => $cgi->param('action')});
}

my $format = $template->get_format("reports/report", $formatparam,
                                   scalar($cgi->param('ctype')));

# If we get a template or CGI error, it comes out as HTML, which isn't valid
# PNG data, and the browser just displays a "corrupt PNG" message. So, you can
# set debug=1 to always get an HTML content-type, and view the error.
$format->{'ctype'} = "text/html" if $cgi->param('debug');

my @time = localtime(time());
my $date = sprintf "%04d-%02d-%02d", 1900+$time[5],$time[4]+1,$time[3];
my $filename = "report-$date.$format->{extension}";
print $cgi->header(-type => $format->{'ctype'},
                   -content_disposition => "inline; filename=$filename");

# Problems with this CGI are often due to malformed data. Setting debug=1
# prints out both data structures.
if ($cgi->param('debug')) {
    require Data::Dumper;
    print "<pre>data hash:\n";
    print html_quote(Data::Dumper::Dumper(%data)) . "\n\n";
    print "data array:\n";
    print html_quote(Data::Dumper::Dumper(@image_data)) . "\n\n</pre>";
}

# All formats point to the same section of the documentation.
$vars->{'doc_section'} = 'reporting.html#reports';

disable_utf8() if ($format->{'ctype'} =~ /^image\//);

$template->process("$format->{'template'}", $vars)
  || ThrowTemplateError($template->error());

exit;


sub get_names {
    my ($names, $isnumeric, $field) = @_;
  
    # These are all the fields we want to preserve the order of in reports.
    my %fields = ('priority'     => get_legal_field_values('priority'),
                  'bug_severity' => get_legal_field_values('bug_severity'),
                  'rep_platform' => get_legal_field_values('rep_platform'),
                  'op_sys'       => get_legal_field_values('op_sys'),
                  'bug_status'   => get_legal_field_values('bug_status'),
                  'resolution'   => [' ', @{get_legal_field_values('resolution')}]);
    
    my $field_list = $fields{$field};
    my @sorted;
    
    if ($field_list) {
        my @unsorted = keys %{$names};
        
        # Extract the used fields from the field_list, in the order they 
        # appear in the field_list. This lets us keep e.g. severities in
        # the normal order.
        #
        # This is O(n^2) but it shouldn't matter for short lists.
        @sorted = map {lsearch(\@unsorted, $_) == -1 ? () : $_} @{$field_list};
    }  
    elsif ($isnumeric) {
        # It's not a field we are preserving the order of, so sort it 
        # numerically...
        sub numerically { $a <=> $b }
        @sorted = sort numerically keys(%{$names});
    } else {
        # ...or alphabetically, as appropriate.
        @sorted = sort(keys(%{$names}));
    }
  
    return \@sorted;
}
sub LookupNamedReport {
    my ($name, $sharer_id, $throw_error) = @_;
    my $user = Bugzilla->login(LOGIN_REQUIRED);
    my $dbh = Bugzilla->dbh;
    my $owner_id;
    $throw_error = 1 unless defined $throw_error;

    # $name and $sharer_id are safe -- we only use them below in SELECT
    # placeholders and then in error messages (which are always HTML-filtered).
    $name || ThrowUserError("report_name_missing");
    trick_taint($name);
    if ($sharer_id) {
        $owner_id = $sharer_id;
        detaint_natural($owner_id);
        $owner_id || ThrowUserError('illegal_user_id', {'userid' => $sharer_id});
    }
    else {
        $owner_id = $user->id;
    }

    my @args = ($owner_id, $name);
    my $extra = '';
    my ($id, $result) = $dbh->selectrow_array("SELECT id, report
                                                 FROM namedreports
                                                WHERE userid = ? AND name = ?
                                                      $extra",
                                               undef, @args);
    if (!defined($result)) {
        return 0 unless $throw_error;
        ThrowUserError("missing_report", {'reportname' => $name,
                                         'sharer_id' => $sharer_id});
    }

    if ($sharer_id) {
        my $group = $dbh->selectrow_array('SELECT group_id
                                             FROM namedreport_group_map
                                            WHERE namedreport_id = ?',
                                          undef, $id);
        if (!grep {$_ == $group} values(%{$user->groups()})) {
            ThrowUserError("missing_report", {'reportname' => $name,
                                             'sharer_id' => $sharer_id});
        }
    }

    $result
       || ThrowUserError("buglist_parameters_required", {'reportname' => $name});

    return $result;
}

# Inserts a Named Report (a "Saved Report") into the database, or
# updates a Named Report that already exists..
# Takes four arguments:
# userid - The userid who the Named Report will belong to.
# report_name - A string that names the new Named Report, or the name
#              of an old Named Report to update. If this is blank, we
#              will throw a UserError. Leading and trailing whitespace
#              will be stripped from this value before it is inserted
#              into the DB.
# report - The report part of the buglist.cgi URL, unencoded. Must not be
#         empty, or we will throw a UserError.
# link_in_footer (optional) - 1 if the Named Report should be
# displayed in the user's footer, 0 otherwise.
# bug IDs only, 0 otherwise (default).
#
# All parameters are validated before passing them into the database.
#
# Returns: A boolean true value if the report existed in the database
# before, and we updated it. A boolean false value otherwise.
sub InsertNamedReport {
    my ($report_name, $report, $link_in_footer) = @_;
    my $dbh = Bugzilla->dbh;

    $report_name = trim($report_name);
    my ($report_obj) = grep {$_->name eq $report_name} @{Bugzilla->user->reports};

    if ($report_obj) {
        $report_obj->set_url($report);
        $report_obj->update();
    } else {
        Bugzilla::Search::SavedReport->create({
            name           => $report_name,
            report          => $report,
            link_in_footer => $link_in_footer
        });
    }

    return $report_obj ? 1 : 0;
}
