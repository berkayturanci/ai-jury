class AiJury < Formula
  include Language::Python::Virtualenv

  desc "Cross-vendor multi-agent PR & code review jury"
  homepage "https://ai-jury.dev/"
  url "https://files.pythonhosted.org/packages/1a/64/ec5bc1828ed3d66307884a839862cb5c64448d577013b80eed1663114e88/ai_jury-1.15.1.tar.gz"
  sha256 "9dfe787e174510f61e1fe4736b3d358e70c2d7fb522123009043e9067b649ce8"
  license "MIT"

  depends_on "python@3.13"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "jury 1.15.1", shell_output("#{bin}/jury --version")
    assert_match "error: provide one of", shell_output("#{bin}/jury --mock 2>&1", 1)
  end
end
