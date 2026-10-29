import unittest

from apple_notes_sync.convert import html_to_markdown


class ConvertTest(unittest.TestCase):
    def check(self, html, expected):
        self.assertEqual(html_to_markdown(html), expected)

    def test_lines_and_blank_lines(self):
        self.check(
            "<div><h1>Title</h1></div><div>one</div><div><br></div><div>two</div>",
            "# Title\none\n\ntwo\n",
        )

    def test_repeated_blank_lines_collapse(self):
        self.check("<div>a</div><div><br></div><div><br></div><div><br></div><div>b</div>", "a\n\nb\n")

    def test_br_inside_div(self):
        self.check("<div>a<br>b<br></div>", "a\nb\n")

    def test_inline_styles(self):
        self.check(
            "<div><b>bold</b> <i>it</i> <u>under</u> <strike>gone</strike> <tt>x()</tt></div>",
            "**bold** *it* under ~~gone~~ `x()`\n",
        )

    def test_style_whitespace_stays_outside_markers(self):
        self.check("<div>a<b> b </b>c</div>", "a **b** c\n")

    def test_adjacent_style_runs_merge(self):
        self.check("<div><b>a</b><b>b</b> <b><i>c</i></b><b><i>d</i></b></div>", "**ab** ***cd***\n")

    def test_bold_heading_is_unwrapped(self):
        self.check("<div><h2><b>Head</b></h2></div>", "## Head\n")

    def test_links(self):
        self.check(
            '<div><a href="https://x.dev">site</a> <a href="https://y.dev">https://y.dev</a></div>',
            "[site](https://x.dev) <https://y.dev>\n",
        )

    def test_dash_list_with_nesting(self):
        self.check(
            '<ul class="Apple-dash-list"><li>a</li><ul class="Apple-dash-list"><li>b</li></ul><li>c</li></ul>',
            "- a\n    - b\n- c\n",
        )

    def test_list_nested_inside_item(self):
        self.check("<ol><li>a<ol><li>b</li></ol></li><li>c</li></ol>", "1. a\n    1. b\n2. c\n")

    def test_list_item_with_divs(self):
        self.check("<ul><li><div>a</div></li><li><div><b>b</b></div></li></ul>", "- a\n- **b**\n")

    def test_table(self):
        self.check(
            "<div><table><tbody><tr><td><div>h1</div></td><td><div>h|2</div></td></tr>"
            "<tr><td><div>a</div></td><td><div>b</div></td></tr></tbody></table></div>",
            "| h1 | h\\|2 |\n|---|---|\n| a | b |\n",
        )

    def test_images_and_data_uris_dropped(self):
        self.check(
            '<div>see <img src="data:image/png;base64,AAAA" style="max-width: 100%;"></div>',
            "see [image]\n",
        )

    def test_entities_and_nbsp(self):
        self.check("<div>a&nbsp;&amp;&lt;b&gt;</div>", "a &<b>\n")

    def test_whitespace_between_tags_ignored(self):
        self.check("<div>a</div>\n<div>b</div>\n", "a\nb\n")

    def test_empty(self):
        self.check("", "")


if __name__ == "__main__":
    unittest.main()
