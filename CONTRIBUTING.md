# Contributing to ha-ep-cube

Thank you for taking an interest. This is a small project I look after in my spare time, and help of any size is genuinely welcome, whether or not you write code.

## Ways to help

- **Tell me what happens on your cube.** The EP Cube cloud behaves a little differently between regions and models, so a report from a setup I don't have is very useful. Bug reports, "this works for me" notes and screenshots all help.
- **Share logs.** A `homeDeviceInfo raw=` line from the debug log, or a trace of something odd, helps me match the integration to more cubes. Please remove anything personal first (see below).
- **Improve the documentation.** If something confused you, it probably confuses others. Fixes to the README, the troubleshooting guide or the dashboard instructions are very welcome.
- **Send code.** Fixes and new features are both welcome, and a pull request doesn't have to be polished. If you'd like to talk the idea through first, open an issue and we can work it out together. At the end of the day the aim is to build the best integration possible for Home Assistant (and Predbat).

## Sending a pull request

1. Branch from `main`. A focused change (one fix or one feature) is easiest to review.
2. Add or update tests where you can, and run them with `pip install -r requirements-test.txt` and then `pytest tests`. If you get stuck on the tests, say so in the PR and I'll help.
3. CI runs Hassfest, the HACS checks and the tests. If one of them fails and the reason isn't clear, ask and I'll take a look.
4. If people will see the change, a line for the release notes is a nice touch.

I'll reply as soon as I can. It's usually within a few days, though it can be longer when I'm busy.

## A note on licensing

The project is under the [MIT licence](LICENSE), so anyone can use, change and share it freely. When you send something in, it joins the project under that same licence. You keep the copyright in your work, your commits keep your name, and I credit contributors in the README and the release notes.

To keep things clean, please send code you wrote yourself, or code you have the right to share under MIT. If your change builds on someone else's work, just tell me which project and under what licence.

If you've already built something in a fork and think it might be useful here, please tell me about it in an issue. I'd be glad to look, and I'll treat what you offer as shared under MIT unless you say otherwise.

## Looking after your privacy

The cube's cloud replies can contain your serial number, location and account details. Please redact anything personal before you post a log or a capture, and never share your EP Cube password, tokens or cookies.

Thanks again, and happy tinkering.
